"""Prueba del modelo externo (OpenAI-compatible) con un Agent real de Strands y herramientas.
Levanta un servidor falso que habla SSE como las APIs de Mistral/Groq/Gemini.
Ejecutar desde la raiz del proyecto con el entorno de Python de los agentes:
    python tests/test_llm_provider.py
"""
import json, os, sys, threading, pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_DIR = next(p for p in (ROOT / "app/OrchestratorAgent", ROOT / "OrchestratorAgent") if p.exists())
sys.path.insert(0, str(AGENT_DIR))

calls = {"n": 0, "seen": [], "fail_once": True}


def sse(handler, chunks):
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.end_headers()
    for c in chunks:
        handler.wfile.write(("data: " + (c if isinstance(c, str) else json.dumps(c)) + "\n\n").encode())
        handler.wfile.flush()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert self.headers["Authorization"] == "Bearer test-key"
        assert body["stream"] is True and body["model"] == "fake-model"
        calls["seen"].append(body)
        if calls["fail_once"]:                       # prueba de reintento con 429
            calls["fail_once"] = False
            self.send_response(429); self.send_header("Retry-After", "0"); self.end_headers()
            self.wfile.write(b'{"error":"rate"}'); return
        roles = [m["role"] for m in body["messages"]]
        if "tool" not in roles:                      # 1a vuelta: pide herramienta (argumentos en 2 fragmentos)
            sse(self, [
                {"choices": [{"delta": {"role": "assistant", "content": "Voy a calcular. "}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                                        "function": {"name": "sumar", "arguments": "{\"a\": 2,"}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": " \"b\": 3}"}}]},
                              "finish_reason": "tool_calls"}]},
                "[DONE]"])
        else:                                        # 2a vuelta: responde con el resultado de la herramienta
            res = next(m for m in body["messages"] if m["role"] == "tool")
            assert res["tool_call_id"] == "call_1" and "5" in res["content"]
            sse(self, [{"choices": [{"delta": {"content": "El resultado es "}}]},
                       {"choices": [{"delta": {"content": "5."}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}},
                       "[DONE]"])


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

os.environ.update(LLM_PROVIDER="openai_compat", LLM_BASE_URL=f"http://127.0.0.1:{srv.server_port}",
                  LLM_MODEL_ID="fake-model", LLM_API_KEY="test-key", AWS_DEFAULT_REGION="us-east-1")
import llm_provider
from strands import Agent, tool


@tool
def sumar(a: int, b: int) -> int:
    """Suma dos numeros enteros."""
    return a + b


# 1) por defecto (sin variable) devuelve el id de Bedrock
os.environ["LLM_PROVIDER"] = "bedrock"
assert llm_provider.build_model("amazon.nova-pro-v1:0") == "amazon.nova-pro-v1:0"
os.environ["LLM_PROVIDER"] = "openai_compat"
model = llm_provider.build_model()
assert isinstance(model, llm_provider.OpenAICompatModel)

# 2) Agent real: texto + herramienta + reintento 429
agent = Agent(model=model, tools=[sumar], system_prompt="Eres un asistente.", callback_handler=None)
res = agent("Cuanto es 2+3?")
text = str(res)
print("respuesta:", text)
assert "5" in text, text
assert calls["n"] == 0 and len(calls["seen"]) == 3          # 429 + vuelta 1 + vuelta 2
tools_sent = calls["seen"][1]["tools"]
assert tools_sent[0]["function"]["name"] == "sumar"
assert tools_sent[0]["function"]["parameters"]["properties"]["a"]["type"] == "integer"
assert calls["seen"][1]["messages"][0] == {"role": "system", "content": "Eres un asistente."}

# 3) streaming de eventos de Strands (lo que usa el Orchestrator para SSE)
import asyncio
async def stream_it():
    texts, tool_events = [], 0
    a2 = Agent(model=model, tools=[sumar], callback_handler=None)
    async for ev in a2.stream_async("Cuanto es 2+3?"):
        if "data" in ev: texts.append(ev["data"])
        if "current_tool_use" in ev: tool_events += 1
    return "".join(texts), tool_events
t, n = asyncio.run(stream_it())
print("stream:", t, "| eventos de herramienta:", n)
assert "5" in t and n >= 1

# 4) simplificacion de esquemas (anyOf con null)
s = llm_provider._simplify_schema({"title": "X", "type": "object", "properties": {"q": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None, "title": "Q"}}})
assert s["properties"]["q"] == {"type": "string"}, s

# 5) error 401 sin reintentos infinitos
class H401(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.send_response(401); self.end_headers(); self.wfile.write(b'{"error":"bad key"}')
bad = ThreadingHTTPServer(("127.0.0.1", 0), H401)
threading.Thread(target=bad.serve_forever, daemon=True).start()
m2 = llm_provider.OpenAICompatModel(f"http://127.0.0.1:{bad.server_port}", "x", lambda: "k")
async def boom():
    async for _ in m2.stream([{"role": "user", "content": [{"text": "hola"}]}]): pass
try:
    asyncio.run(boom()); raise SystemExit("debio fallar")
except RuntimeError as exc:
    print("401 ->", str(exc)[:80]); assert "401" in str(exc)

# 6) estilo Gemini: llamadas paralelas SIN index ni id, con firma de pensamiento (extra_content) que hay que devolver
gem_seen = []
class HG(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"]))); gem_seen.append(body)
        if not any(m["role"] == "tool" for m in body["messages"]):
            sse(self, [
                {"choices": [{"delta": {"tool_calls": [{"function": {"name": "sumar", "arguments": "{\"a\": 1, \"b\": 1}"},
                                                         "extra_content": {"google": {"thought_signature": "SIG1"}}}]}}]},
                {"choices": [{"delta": {"tool_calls": [{"function": {"name": "sumar", "arguments": "{\"a\": 10, \"b\": 5}"}}]},
                              "finish_reason": "stop"}]},
                "[DONE]"])
        else:
            sse(self, [{"choices": [{"delta": {"content": "Listo: 2 y 15."}, "finish_reason": "stop"}]}, "[DONE]"])
gsrv = ThreadingHTTPServer(("127.0.0.1", 0), HG)
threading.Thread(target=gsrv.serve_forever, daemon=True).start()
gm = llm_provider.OpenAICompatModel(f"http://127.0.0.1:{gsrv.server_port}", "gem", lambda: "k")
ga = Agent(model=gm, tools=[sumar], callback_handler=None)
gres = str(ga("suma"))
print("gemini-style:", gres)
assert "15" in gres
second = gem_seen[1]["messages"]
asst = next(m for m in second if m["role"] == "assistant")
assert len(asst["tool_calls"]) == 2, asst                       # dos llamadas, no fusionadas
assert json.loads(asst["tool_calls"][1]["function"]["arguments"]) == {"a": 10, "b": 5}
assert asst["tool_calls"][0]["extra_content"] == {"google": {"thought_signature": "SIG1"}}
tool_msgs = [m for m in second if m["role"] == "tool"]
assert len(tool_msgs) == 2 and all(m.get("name") == "sumar" for m in tool_msgs)
print("llm_provider OK")
