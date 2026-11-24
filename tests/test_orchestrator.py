"""Prueba del Orchestrator con un modelo simulado (no usa AWS).
Ejecutar desde la raiz del proyecto con el entorno de Python de los agentes:
    python tests/test_orchestrator.py
"""
import sys, json, asyncio, importlib.util, os
os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x",
                  FLIGHTS_AGENT_URL="http://f", WEATHER_AGENT_URL="http://w")
import pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN = next(p for p in (ROOT / "app/OrchestratorAgent/main.py", ROOT / "OrchestratorAgent/main.py") if p.exists())
spec = importlib.util.spec_from_file_location("omain", str(MAIN)); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from strands.models import Model

class Stub(Model):
    def __init__(self): self.n = 0; self.seen = []
    def update_config(self, **k): pass
    def get_config(self): return {}
    async def structured_output(self, *a, **k):
        if False: yield
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        self.n += 1; self.seen.append(len(messages))
        yield {"messageStart": {"role": "assistant"}}
        def tool(tid, name, inp):
            return [{"contentBlockStart": {"start": {"toolUse": {"toolUseId": tid, "name": name}}}},
                    {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(inp)}}}},
                    {"contentBlockStop": {}}]
        if self.n == 1:
            yield {"contentBlockDelta": {"delta": {"text": "Consultando clima. "}}}; yield {"contentBlockStop": {}}
            for e in tool("t1", "ask_weather", {"city": "Cartagena", "start_date": "04/12/2026", "end_date": "07/12/2026"}): yield e
            stop = "tool_use"
        elif self.n == 2:
            for e in tool("t2", "create_trip", {"userId": "miller", "origin": "Bogota", "destination": "Cartagena", "startDate": "04/12/2026", "endDate": "07/12/2026", "travelers": 3, "budget": 5000000}): yield e
            for e in tool("t3", "calculate_budget", {"budget_cop": 5000000, "travelers": 3, "days": 4, "flight_price_usd_per_person": 60}): yield e
            stop = "tool_use"
        else:
            yield {"contentBlockDelta": {"delta": {"text": "Listo, viaje guardado."}}}; yield {"contentBlockStop": {}}
            stop = "end_turn"
        yield {"messageStop": {"stopReason": stop}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}}

stub = Stub(); m.MODEL_ID = stub
m._call_a2a_agent = lambda url, text: {"city": "Cartagena", "source": "pronostico", "days": [{"date": "2026-12-04", "temp_max_c": 31}]}
class FakeLambda:
    def invoke(self, FunctionName, InvocationType, Payload):
        import io; p = json.loads(Payload)
        print("   [lambda]", FunctionName, list(p))
        return {"Payload": io.BytesIO(json.dumps({"tripId": "T-1", **p}).encode())}
m._lambda_client = FakeLambda()

async def run(payload):
    out = []
    async for ev in m.invoke(payload, type("C", (), {"session_id": "s"*40})()):
        out.append(ev)
    return out
ev = asyncio.run(run({"prompt": "Cartagena", "userId": "miller", "context": {"a": 1},
                      "history": [{"role": "assistant", "content": "hola"}, {"role": "user", "content": "hola"}, {"role": "assistant", "content": "en que te ayudo"}]}))
for e in ev: print(json.dumps(e, ensure_ascii=False)[:170])
print("modelo llamado", stub.n, "veces; mensajes vistos:", stub.seen)
# historial invalido
print(m._history_to_messages([{"role":"assistant","content":"x"},{"role":"user","content":"a"},{"role":"user","content":"b"},{"role":"assistant","content":"c"},{"role":"user","content":"d"}]))
print(m._extract_json('```json\n{"a":1}\n```'), m._extract_json('texto {"b":2} fin'), m._extract_json('nada'))

print("---- SSE real via /invocations ----")
from starlette.testclient import TestClient
stub.n = 0
c = TestClient(m.app)
print("ping:", c.get("/ping").status_code, c.get("/ping").text)
with c.stream("POST", "/invocations", json={"prompt": "hola", "userId": "miller"}, headers={"X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "a"*40}) as r:
    print(r.status_code, r.headers.get("content-type"))
    raw = b"".join(r.iter_raw())
print(raw.decode()[:700])

print("---- Filtro <thinking> y continuacion automatica ----")
f = m._ThinkingFilter()
pieces = ["Hola <thi", "nking>razono", " mucho</thin", "king>\nTexto visible", " y <thinking>otro</thinking>fin."]
vis = "".join(f.feed(p) for p in pieces) + f.flush()
print("visible:", repr(vis))
assert vis == "Hola Texto visible y fin.", vis
f = m._ThinkingFilter(); assert (f.feed("a < b y 3 <") + f.flush()) == "a < b y 3 <"      # no confunde un '<' normal

class Stub2(Model):
    def __init__(self): self.n = 0
    def update_config(self, **k): pass
    def get_config(self): return {}
    async def structured_output(self, *a, **k):
        if False: yield
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        self.n += 1
        yield {"messageStart": {"role": "assistant"}}
        if self.n == 1:       # se queda a medias, como hizo Nova Pro en AWS
            yield {"contentBlockDelta": {"delta": {"text": "<thin"}}}
            yield {"contentBlockDelta": {"delta": {"text": "king>razonando</thinking>Mientras tanto, calcularé el presupuesto. Por favor, espera un momento."}}}
            yield {"contentBlockStop": {}}; stop = "end_turn"
        elif self.n == 2:     # tras el empujon llama a la herramienta
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": "b1", "name": "calculate_budget"}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps({"budget_cop": 5000000, "travelers": 3, "days": 4, "flight_price_usd_per_person": 72})}}}}
            yield {"contentBlockStop": {}}; stop = "tool_use"
        else:
            yield {"contentBlockDelta": {"delta": {"text": "Resumen final del viaje."}}}; yield {"contentBlockStop": {}}; stop = "end_turn"
        yield {"messageStop": {"stopReason": stop}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}}
stub2 = Stub2(); m.MODEL_ID = stub2
ev2 = asyncio.run(run({"prompt": "Cambia el presupuesto de mi plan a 5 millones", "userId": "miller"}))
texts = "".join(e["data"] for e in ev2 if e["type"] == "text")
print("textos:", repr(texts)); print("tipos:", [e["type"] for e in ev2])
assert "thinking" not in texts and "razonando" not in texts
assert stub2.n == 3, stub2.n                                     # turno inconcluso -> empujon -> herramienta -> resumen
assert any(e["type"] == "tool_result" and e["name"] == "calculate_budget" for e in ev2)
assert texts.endswith("Resumen final del viaje.") and ev2[-1]["type"] == "done"

# un resumen normal NO debe disparar el reintento
class Stub3(Stub2):
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        self.n += 1
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": "Aqui tienes tu viaje. Quieres que reserve el vuelo mas barato?"}}}; yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}}
stub3 = Stub3(); m.MODEL_ID = stub3
asyncio.run(run({"prompt": "hola", "userId": "miller"})); assert stub3.n == 1, stub3.n
print("OK: filtro y continuacion automatica")

print("---- Saludo: sin herramientas y sin retomar pedidos antiguos ----")
for g in ["Hola", "hola!", "Buenas tardes", "¡Hola, buenos dias!", "Gracias", "hey"]:
    assert m._is_greeting(g), g
for g in ["Hola, quiero ir a Cartagena", "ok", "si", "listo", "reserva el vuelo mas barato", "Quiero organizar unas vacaciones", "como estas planificando"]:
    assert not m._is_greeting(g), g

class Rec(Model):
    def __init__(self): self.calls = []
    def update_config(self, **k): pass
    def get_config(self): return {}
    async def structured_output(self, *a, **k):
        if False: yield
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        self.calls.append({"tools": [t.get("name") or t.get("toolSpec", {}).get("name") for t in (tool_specs or [])], "system": system_prompt})
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": "Hola, voy a ayudarte. A donde quieres viajar?"}}}; yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}}
rec = Rec(); m.MODEL_ID = rec
ev3 = asyncio.run(run({"prompt": "Hola", "userId": "miller"}))
assert rec.calls and rec.calls[0]["tools"] == [], rec.calls[0]["tools"]
assert "SOLO esta saludando" in rec.calls[0]["system"] and len(rec.calls) == 1
rec2 = Rec(); m.MODEL_ID = rec2
ev4 = asyncio.run(run({"prompt": "Cambia las fechas de mi viaje al 10 de enero", "userId": "miller"}))
print([ (e["type"], e.get("message")) for e in ev4 if e["type"] in ("error",)])
assert "ask_weather" in rec2.calls[0]["tools"] and "SOLO esta saludando" not in rec2.calls[0]["system"]
assert "SOLO contexto de apoyo" in rec2.calls[0]["system"]
assert "delete_reservation" in rec2.calls[0]["tools"] and "ELIMINAR una reserva" in rec2.calls[0]["system"]
print("OK: saludo sin herramientas; pedido real con herramientas ->", len(rec2.calls[0]["tools"]))
assert "NUNCA inventes el origen" in rec2.calls[0]["system"] and "Usa solo lugares y actividades reales" in rec2.calls[0]["system"]
print("OK: el prompt exige preguntar origen, viajeros y presupuesto cuando faltan")

print("---- Datos faltantes: pregunta determinista del codigo (sin modelo ni herramientas) ----")
rec3 = Rec(); m.MODEL_ID = rec3
ev3 = asyncio.run(run({"prompt": "quiero irme 4 dias a santa marta", "userId": "miller"}))
ask3 = "".join(e["data"] for e in ev3 if e["type"] == "text")
assert not rec3.calls, "la pregunta de datos faltantes no debe depender del modelo"
assert ask3.startswith("Para armarlo me faltan: la ciudad de salida, cuántas personas viajan y tu presupuesto en pesos."), ask3
assert not any(e["type"] in ("plan", "tool_start") for e in ev3) and ev3[-1]["type"] == "done"
# segunda vuelta: ya dio los datos -> el codigo conduce el plan (linea de tiempo) y el LLM solo redacta el resumen
FL = {"flights": [{"airline": "LATAM", "flight": "LA4126", "depart": "12:00", "arrive": "13:30", "price_usd": 77, "stops": 0}]}
WX = {"city": "Santa Marta", "source": "pronostico", "days": [{"date": "2026-12-04", "condition": "Parcialmente nublado", "temp_min_c": 26, "temp_max_c": 31, "rain_probability_pct": 20}]}
m._call_a2a_agent = lambda url, text: FL if url == m.FLIGHTS_URL else WX
hist = [{"role": "user", "content": "quiero irme 4 dias a santa marta"},
        {"role": "assistant", "content": "Para armarlo me faltan: la ciudad de salida, cuantas personas viajan y tu presupuesto en pesos."}]
rec4 = Rec(); m.MODEL_ID = rec4
ev5 = asyncio.run(run({"prompt": "desde Bogota, somos 2 y tengo 3 millones", "userId": "miller", "history": hist}))
types5 = [e["type"] for e in ev5]
assert "plan" in types5 and "plan_done" in types5 and "suggestions" in types5 and types5[-1] == "done", types5
res5 = {e["name"]: e for e in ev5 if e["type"] == "tool_result"}
assert res5["create_trip"]["data"]["destination"] == "Santa Marta" and res5["create_trip"]["data"]["travelers"] == 2 and res5["create_trip"]["data"]["budget"] == 3000000
last = rec4.calls[-1]
assert last["tools"] == [] and "RESULTADOS REALES" in last["system"] and "Bogotá → Santa Marta" in last["system"]
# el prompt oficial de la entrega: clima y vuelos por A2A, presupuesto de 5.000.000 para 3 viajeros, itinerario guardado
rec5 = Rec(); m.MODEL_ID = rec5
m._call_a2a_agent = lambda url, text: FL if url == m.FLIGHTS_URL else {**WX, "city": "Cartagena"}
ev6 = asyncio.run(run({"prompt": "Quiero organizar unas vacaciones de cuatro días en Cartagena. Salgo desde Bogotá, viajo con dos personas más y tengo un presupuesto de 5 millones de pesos. Quiero conocer el clima, encontrar vuelos y organizar algunas actividades turísticas.", "userId": "miller"}))
res6 = {e["name"]: e for e in ev6 if e["type"] == "tool_result"}
assert [n for n in ("ask_weather", "ask_flights", "calculate_budget", "plan_itinerary", "create_trip", "create_itinerary") if n in res6] == \
    ["ask_weather", "ask_flights", "calculate_budget", "plan_itinerary", "create_trip", "create_itinerary"] or len(res6) == 6
assert res6["calculate_budget"]["data"]["budgetCOP"] == 5000000 and res6["calculate_budget"]["data"]["travelers"] == 3
assert res6["create_trip"]["data"]["travelers"] == 3 and res6["create_trip"]["data"]["origin"] == "Bogotá"
txt6 = "".join(e["data"] for e in ev6 if e["type"] == "text")
assert txt6.strip(), "debe haber un resumen final"
assert rec5.calls[-1]["tools"] == [] and "Vuelo de ida más barato: LATAM LA4126" in rec5.calls[-1]["system"]
print("OK: preguntar lo que falta, y planificar cuando ya esta todo")

print("---- Catalogo de lugares reales en el prompt ----")
sp = rec2.calls[0]["system"]
assert "Tayrona" in sp and "Castillo San Felipe de Barajas" in sp and "LUGARES PARA EL ITINERARIO" in sp
amur = [l for l in sp.splitlines() if "Ciudad Amurallada y Centro Histórico" in l]
assert len(amur) == 1 and amur[0].startswith("- Cartagena:"), amur      # la Ciudad Amurallada solo aparece bajo Cartagena
assert "No nombres restaurantes" in sp
print("OK: el prompt trae lugares reales por ciudad;", len(m._PLACES), "destinos")


print("---- Especialistas: peticion limpia, reintento y sin <thinking> ----")
assert m._clean_place("Barranquilla, Atlantico") == "Barranquilla" and m._clean_place(" Santa Marta ") == "Santa Marta"
assert m._to_dmy("2026-12-04") == "04/12/2026" and m._to_dmy("4-12-2026") == "04/12/2026" and m._to_dmy("04/12/2026") == "04/12/2026"
assert m._strip_thinking("<thinking>razono</thinking>\n{\"a\": 1}") == '{"a": 1}'
assert m._strip_thinking("<thinking> sin cerrar, falta el destino. Por favor especifica") == ""
assert m._strip_thinking("<thinking> sin cerrar {\"flights\": []}") == '{"flights": []}'

sent = []
def fake_ok(url, text):
    sent.append(text); return {"origin": "Barranquilla", "destination": "Santa Marta", "flights": [{"airline": "Avianca"}]}
m._call_a2a_agent = fake_ok; m.FLIGHTS_URL = "http://f"
out = json.loads(m.ask_flights(origin="Barranquilla, Atlantico", destination="Santa Marta", date="2026-12-04"))
assert out["flights"] and "Origen: Barranquilla. Destino: Santa Marta. Fecha de salida: 04/12/2026" in sent[0], sent[0]
assert "origin='Barranquilla'" in sent[0] and "Atlantico" not in sent[0]

# el especialista responde primero con razonamiento y sin JSON (lo que paso en AWS) y luego bien: se reintenta una vez
seq = [{"raw": "Por favor, especifica el destino al que deseas volar"}, {"flights": [{"airline": "LATAM"}]}]
sent2 = []
def fake_retry(url, text):
    sent2.append(text); return seq[len(sent2) - 1]
m._call_a2a_agent = fake_retry
out = json.loads(m.ask_flights(origin="Barranquilla", destination="Santa Marta", date="04/12/2026"))
assert out["flights"][0]["airline"] == "LATAM" and len(sent2) == 2 and "UNICAMENTE el objeto JSON" in sent2[1]
# si falla dos veces, devuelve un error claro (el Orchestrator lo cuenta, no se rompe)
m._call_a2a_agent = lambda url, text: {"raw": "<thinking> no se"}
out = json.loads(m.ask_flights(origin="Barranquilla", destination="Santa Marta", date="04/12/2026"))
assert "error" in out and "no devolvio un resultado valido" in out["error"]
# un error propio del especialista (p. ej. sin vuelos) NO se reintenta
n = []
m._call_a2a_agent = lambda url, text: (n.append(1) or {"error": "No se encontraron vuelos reales para esa ruta o fecha."})
out = json.loads(m.ask_flights(origin="Bogota", destination="Leticia", date="04/12/2026"))
assert "No se encontraron" in out["error"] and len(n) == 1
# clima: misma logica
m._call_a2a_agent = lambda url, text: {"city": "Cartagena", "days": [{"date": "2026-12-04"}], "_t": text}
m.WEATHER_URL = "http://w"
ow = json.loads(m.ask_weather(city="Cartagena, Bolivar", start_date="04/12/2026", end_date="08/12/2026"))
assert ow["days"] and "Ciudad: Cartagena. Desde 04/12/2026 hasta 08/12/2026" in ow["_t"]
print("OK: peticion limpia, reintento ante respuesta sin JSON, error claro y <thinking> eliminado")
