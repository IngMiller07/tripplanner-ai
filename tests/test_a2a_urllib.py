"""Prueba de la llamada A2A del Orchestrator (SigV4 + urllib) contra un servidor local.
Ejecutar desde la raiz del proyecto con el entorno de Python de los agentes:
    python tests/test_a2a_urllib.py
"""
import os, json, threading, importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer
os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="AKIDEXAMPLE", AWS_SECRET_ACCESS_KEY="x" * 40,
                  FLIGHTS_AGENT_URL="http://f", WEATHER_AGENT_URL="http://w")
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN = next(p for p in (ROOT / "app/OrchestratorAgent/main.py", ROOT / "OrchestratorAgent/main.py") if p.exists())
spec = importlib.util.spec_from_file_location("omain", str(MAIN)); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n))
        assert "Authorization" in self.headers and "AWS4-HMAC-SHA256" in self.headers["Authorization"]
        assert body["method"] == "message/send"
        mode = self.path
        if mode == "/ok":
            out = {"jsonrpc": "2.0", "id": body["id"], "result": {"parts": [{"kind": "text", "text": "```json\n{\"city\":\"Cartagena\",\"days\":[]}\n```"}]}}
            self.send_response(200)
        elif mode == "/err":
            out = {"message": "forbidden"}; self.send_response(403)
        else:
            out = None; self.send_response(200)
        self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write((json.dumps(out) if out is not None else "no es json").encode())

srv = HTTPServer(("127.0.0.1", 0), H); port = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"
r = m._call_a2a_agent(base + "/ok", "clima"); print("ok  ->", r); assert r.get("city") == "Cartagena"
r = m._call_a2a_agent(base + "/err", "clima"); print("403 ->", r); assert "403" in r["error"]
r = m._call_a2a_agent(base + "/raw", "clima"); print("raw ->", r); assert "raw" in r
r = m._call_a2a_agent("http://127.0.0.1:1/x", "clima"); print("down->", r); assert "No se pudo contactar" in r["error"]
print("A2A urllib OK")
