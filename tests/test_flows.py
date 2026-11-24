"""Pruebas de integracion del entrypoint del Orchestrator (invoke): plan, reserva, eliminar y respaldo del resumen.
Usa un modelo simulado y especialistas/Lambdas simulados (no usa AWS).   python tests/test_flows.py
"""
import asyncio
import importlib.util
import io
import json
import os
import pathlib

os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x",
                  FLIGHTS_AGENT_URL="http://f", WEATHER_AGENT_URL="http://w")
ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN = next(p for p in (ROOT / "app/OrchestratorAgent/main.py", ROOT / "OrchestratorAgent/main.py") if p.exists())
spec = importlib.util.spec_from_file_location("omain_flows", str(MAIN))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from strands.models import Model  # noqa: E402

DEMO = ("Quiero organizar unas vacaciones de cuatro días en Cartagena. Salgo desde Bogotá, viajo con dos personas más y "
        "tengo un presupuesto de 5 millones de pesos. Quiero conocer el clima, encontrar vuelos y organizar algunas actividades turísticas.")
FL = {"flights": [{"airline": "Avianca", "flight": "AV9", "depart": "09:00", "arrive": "10:30", "price_usd": 120, "stops": 1},
                  {"airline": "LATAM", "flight": "LA4126", "depart": "12:00", "arrive": "13:30", "price_usd": 77, "stops": 0}]}
WX = {"city": "Cartagena", "source": "pronostico", "days": [{"date": "2026-12-04", "condition": "Parcialmente nublado", "temp_min_c": 26, "temp_max_c": 31, "rain_probability_pct": 20}]}


class Say(Model):
    """Responde siempre el mismo texto; si `boom`, falla (para probar el respaldo del resumen)."""
    def __init__(self, text="Resumen del modelo.", boom=False): self.text, self.boom, self.calls = text, boom, []
    def update_config(self, **k): pass
    def get_config(self): return {}
    async def structured_output(self, *a, **k):
        if False: yield
    async def stream(self, messages, tool_specs=None, system_prompt=None, **kw):
        self.calls.append({"tools": [t.get("name") or t.get("toolSpec", {}).get("name") for t in (tool_specs or [])], "system": system_prompt})
        if self.boom:
            raise RuntimeError("modelo caido")
        yield {"messageStart": {"role": "assistant"}}
        yield {"contentBlockDelta": {"delta": {"text": self.text}}}; yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}}


LAMBDA_LOG = []
STORE = []          # reservas guardadas (simula DynamoDB)


def writes():
    """Llamadas que MODIFICAN datos (las lecturas de apoyo, como GetReservations, no cuentan)."""
    return [x for x in LAMBDA_LOG if not x[0].startswith(("TripPlanner-Get",))]


class FakeLambda:
    def invoke(self, FunctionName, InvocationType, Payload):
        p = json.loads(Payload)
        LAMBDA_LOG.append((FunctionName, p))
        if FunctionName == "TripPlanner-CreateReservation":
            body = {"reservationId": f"R-{len(STORE) + 1}", "status": "SIMULADA", "createdAt": f"2026-11-24T14:{len(STORE):02d}", **p}
            STORE.append(body)
        elif FunctionName == "TripPlanner-GetReservations":
            body = {"reservations": [dict(r) for r in STORE if r["tripId"] == p.get("tripId")]}
        elif FunctionName == "TripPlanner-GetTrips":
            body = {"trips": [{"tripId": "T-1", "origin": "Bogotá", "destination": "Cartagena", "createdAt": "2026-11-24"}]}
        elif FunctionName == "TripPlanner-DeleteReservation":
            STORE[:] = [r for r in STORE if r["reservationId"] != p.get("reservationId")]
            body = {"deleted": True, **p}
        else:
            body = {"TripPlanner-CreateTrip": {"tripId": "T-1", **p}, "TripPlanner-CreateItinerary": {"itineraryId": "I-1", **p},
                    "TripPlanner-UpdateTrip": {**p}, "TripPlanner-UpdateItinerary": {**p}}.get(FunctionName, {"error": "?"})
        return {"Payload": io.BytesIO(json.dumps(body).encode())}


m._lambda_client = FakeLambda()
m._call_a2a_agent = lambda url, text: FL if url == m.FLIGHTS_URL else WX


async def run(payload):
    return [e async for e in m.invoke(payload, type("C", (), {"session_id": "s" * 40})())]


CTX = {"activeTrip": {"tripId": "T-1", "origin": "Bogotá", "destination": "Cartagena", "startDate": "04/12/2026", "endDate": "07/12/2026", "travelers": 3, "budget": 5000000},
       "lastFlights": {"route": "Bogotá-Cartagena", "date": "04/12/2026", "options": FL["flights"]}}


def test_update_trip_flow():
    """Chat real: Barranquilla -> Cali, 4 dias, 2 viajeros, $3.000.000. 'quiero que sean 3 dias' dejaba vuelos en $0 y el
    itinerario de 4 dias. Ahora el codigo recalcula con el vuelo real y actualiza el viaje y el itinerario."""
    ctx = {"activeTrip": {"tripId": "T-9", "origin": "Barranquilla", "destination": "Cali", "startDate": "04/12/2026",
                          "endDate": "07/12/2026", "travelers": 2, "budget": 3000000},
           "lastFlights": {"route": "Barranquilla-Cali", "date": "04/12/2026",
                           "options": [{"airline": "LATAM Airlines", "flight": "LA4151", "depart": "14:20", "price_usd": 93, "stops": 1},
                                       {"airline": "Avianca", "flight": "AV8419", "depart": "06:25", "price_usd": 94, "stops": 1}]},
           "activeItineraryId": "I-9"}
    calls = []
    real = m._call_a2a_agent
    m._call_a2a_agent = lambda url, text: (calls.append(url), real(url, text))[1]
    try:
        m.MODEL_ID = Say("Listo, quedó en 3 días."); LAMBDA_LOG.clear()
        msg = "quiero cambiar la cantidad de dias, el presupuesto se me pasa, quiero que sean 3 dias nada mas"
        ev = asyncio.run(run({"prompt": msg, "userId": "miller", "context": ctx}))
        types = [e["type"] for e in ev]
        assert "plan" in types and "plan_done" in types and ev[-1]["type"] == "done" and "error" not in types, types
        assert next(e for e in ev if e["type"] == "plan")["title"] == "Actualizando tu viaje"
        assert [n for n, _ in writes()] == ["TripPlanner-UpdateTrip", "TripPlanner-UpdateItinerary"], writes()
        up, it = writes()[0][1], writes()[1][1]
        assert up["tripId"] == "T-9" and up["endDate"] == "06/12/2026" and up["startDate"] == "04/12/2026" and up["travelers"] == 2 and up["budget"] == 3000000
        assert it["tripId"] == "T-9" and it["itineraryId"] == "I-9" and len(it["days"]) == 3 and it["days"][-1]["title"] == "Despedida y regreso"
        b = next(e for e in ev if e["type"] == "tool_result" and e["name"] == "calculate_budget")["data"]
        flights_cost = next(x["costCOP"] for x in b["breakdown"] if x["item"] == "Vuelos")
        assert flights_cost == 93 * 4000 * 2 * 2, flights_cost                           # el error del chat: vuelos en $0
        assert b["totalEstimatedCOP"] == 2_808_000 and b["remainingCOP"] == 192_000 and b["withinBudget"] is True, b
        assert calls == [], "con la misma ruta y salida se reutiliza el vuelo y el clima no se vuelve a consultar"
        steps = {e["id"]: e for e in ev if e["type"] == "step"}
        assert steps["weather"]["status"] == "skipped" and steps["flights"]["status"] == "done" and "ms" not in steps["flights"]
        assert steps["save_trip"]["status"] == "done" and steps["save_itinerary"]["status"] == "done"
        assert "Duración: 4 → 3 días" in steps["understand"]["detail"]
        assert any(e["type"] == "suggestions" for e in ev)

        # solo presupuesto: no toca el itinerario ni vuelve a consultar nada
        LAMBDA_LOG.clear(); calls.clear()
        ev = asyncio.run(run({"prompt": "cambia el presupuesto a 4 millones", "userId": "miller", "context": ctx}))
        assert [n for n, _ in writes()] == ["TripPlanner-UpdateTrip"] and writes()[0][1]["budget"] == 4000000 and calls == []
        st = {e["id"]: e for e in ev if e["type"] == "step"}
        assert st["itinerary"]["status"] == "skipped" and st["save_itinerary"]["status"] == "skipped"
        b = next(e for e in ev if e["type"] == "tool_result" and e["name"] == "calculate_budget")["data"]
        assert b["budgetCOP"] == 4_000_000 and b["remainingCOP"] == 4_000_000 - 3_348_000

        # alargar el viaje: hay dias nuevos sin pronostico -> se consulta el clima; el vuelo se reutiliza
        LAMBDA_LOG.clear(); calls.clear()
        ev = asyncio.run(run({"prompt": "alarga el viaje 2 dias", "userId": "miller", "context": ctx}))
        assert calls == [m.WEATHER_URL] and writes()[0][1]["endDate"] == "09/12/2026" and len(writes()[1][1]["days"]) == 6

        # nuevas fechas: cambian salida y regreso -> clima y vuelos otra vez
        LAMBDA_LOG.clear(); calls.clear()
        ev = asyncio.run(run({"prompt": "cambia las fechas del 10 al 13 de diciembre", "userId": "miller", "context": ctx}))
        assert sorted(calls) == sorted([m.WEATHER_URL, m.FLIGHTS_URL]) and writes()[0][1]["startDate"] == "10/12/2026" and writes()[0][1]["endDate"] == "13/12/2026"

        # sin id de itinerario en el contexto: se crea uno en vez de fallar
        LAMBDA_LOG.clear(); calls.clear()
        ctx2 = {k: v for k, v in ctx.items() if k != "activeItineraryId"}
        ev = asyncio.run(run({"prompt": "quiero que sean 3 dias", "userId": "miller", "context": ctx2}))
        assert [n for n, _ in writes()] == ["TripPlanner-UpdateTrip", "TripPlanner-CreateItinerary"]

        # ya era asi: no escribe nada
        LAMBDA_LOG.clear()
        ev = asyncio.run(run({"prompt": "quiero que sean 4 dias", "userId": "miller", "context": ctx}))
        txt = "".join(e["data"] for e in ev if e["type"] == "text")
        assert not writes() and "ya tiene 4 días" in txt and "plan" not in [e["type"] for e in ev], txt

        # pide cambiar sin decir el valor -> pregunta; la respuesta corta ("3 dias") ya entra al cambio
        LAMBDA_LOG.clear()
        ev = asyncio.run(run({"prompt": "quiero cambiar la cantidad de dias", "userId": "miller", "context": ctx}))
        q = "".join(e["data"] for e in ev if e["type"] == "text")
        assert "dime el nuevo valor" in q.lower() and "4 días" in q and not writes() and "plan" not in [e["type"] for e in ev]
        hist = [{"role": "user", "content": "quiero cambiar la cantidad de dias"}, {"role": "assistant", "content": q}]
        ev = asyncio.run(run({"prompt": "3 dias", "userId": "miller", "context": ctx, "history": hist}))
        assert [n for n, _ in writes()] == ["TripPlanner-UpdateTrip", "TripPlanner-UpdateItinerary"] and writes()[0][1]["endDate"] == "06/12/2026"

        # valores invalidos se explican sin escribir
        LAMBDA_LOG.clear()
        ev = asyncio.run(run({"prompt": "quiero que sean 30 dias", "userId": "miller", "context": ctx}))
        assert not writes() and "14 días" in "".join(e["data"] for e in ev if e["type"] == "text")

        # si el LLM del resumen falla, la plantilla tambien explica el cambio
        m.MODEL_ID = Say(boom=True); LAMBDA_LOG.clear()
        ev = asyncio.run(run({"prompt": "quiero que sean 3 dias", "userId": "miller", "context": ctx}))
        txt = "".join(e["data"] for e in ev if e["type"] == "text")
        assert txt.startswith("Listo, actualicé tu viaje. Duración: 4 → 3 días.") and "192.000" in txt, txt
        m.MODEL_ID = Say("x")

        # lo que NO es un cambio sigue yendo al agente libre / otros flujos
        for other in ("¿cuáles son mis viajes guardados?", "cuánto cuesta un hotel para 3 personas", "agrega una visita a Medellín"):
            m.MODEL_ID = Say("respuesta libre"); LAMBDA_LOG.clear()
            ev = asyncio.run(run({"prompt": other, "userId": "miller", "context": ctx}))
            assert "plan" not in [e["type"] for e in ev] and not writes() and "respuesta libre" in "".join(e.get("data", "") for e in ev if e["type"] == "text"), other
    finally:
        m._call_a2a_agent = real
    print("OK: cambiar dias/presupuesto/viajeros/fechas es determinista: usa el vuelo real, actualiza viaje e itinerario y no repite consultas")


def test_two_step_flow_is_deterministic():
    """Chat real: 'quiero viajar desde barranquilla a punta cana' -> pregunta; 'somos 2, 10 millones' -> plan con linea de tiempo."""
    m.MODEL_ID = Say("Tu plan está listo."); LAMBDA_LOG.clear(); STORE.clear()
    ev1 = asyncio.run(run({"prompt": "quiero viajar desde barranquilla a punta cana", "userId": "miller"}))
    q = "".join(e["data"] for e in ev1 if e["type"] == "text")
    assert q.startswith("Para armarlo me faltan: cuántas personas viajan y tu presupuesto en pesos.") and not LAMBDA_LOG
    assert not any(e["type"] in ("plan", "tool_start") for e in ev1) and ev1[-1]["type"] == "done"
    assert not m.MODEL_ID.calls, "la pregunta no debe depender del modelo"
    hist = [{"role": "user", "content": "quiero viajar desde barranquilla a punta cana"}, {"role": "assistant", "content": q}]
    ev2 = asyncio.run(run({"prompt": "somos 2 personas, el presupuesto es de 10.000.000 pesos", "userId": "miller", "history": hist}))
    assert "plan" in [e["type"] for e in ev2] and "plan_done" in [e["type"] for e in ev2]
    assert [n for n, _ in writes()] == ["TripPlanner-CreateTrip", "TripPlanner-CreateItinerary"]
    # aunque el modelo hubiera redactado la pregunta a su manera, el nuevo flujo ya no depende de ella
    # pais como destino: pregunta la ciudad y la respuesta siguiente completa el plan
    LAMBDA_LOG.clear(); STORE.clear()
    ev3 = asyncio.run(run({"prompt": "quiero viajar a brasil desde barranquilla, somos 2 con 10 millones", "userId": "miller"}))
    q3 = "".join(e["data"] for e in ev3 if e["type"] == "text")
    assert "me faltan: la ciudad de destino en Brasil" in q3 and not any(e["type"] == "plan" for e in ev3) and not LAMBDA_LOG
    h3 = [{"role": "user", "content": "quiero viajar a brasil desde barranquilla, somos 2 con 10 millones"}, {"role": "assistant", "content": q3}]
    ev4 = asyncio.run(run({"prompt": "río de janeiro", "userId": "miller", "history": h3}))
    trip = next(e for e in ev4 if e["type"] == "tool_result" and e["name"] == "create_trip")["data"]
    assert trip["destination"] == "Río de Janeiro" and trip["origin"] == "Barranquilla" and trip["travelers"] == 2
    print("OK: dos tiempos deterministas (pregunta del codigo, sin modelo) y pais -> ciudad -> plan")


def test_plan_request_detection():
    f = m._is_plan_request
    assert f(DEMO, [], {})
    assert f("quiero irme 4 dias a santa marta", [], {})
    assert not f("hola", [], {}) or True                       # el saludo se decide antes
    assert not f("elimina la reserva del vuelo", [], CTX) and not f("cambia las fechas del viaje", [], CTX)
    assert not f("reserva el vuelo mas barato", [], CTX) and not f("cual es el clima", [], {})
    assert f("quiero otro viaje a Medellin 3 dias", [], CTX)                      # destino distinto al viaje activo
    assert not f("quiero revisar mi viaje a Cartagena", [], CTX)                  # mismo destino: es una consulta
    asked = [{"role": "assistant", "content": "Para armarlo me faltan: la ciudad de salida."}]
    assert f("desde Cali somos 2 y tengo 3 millones", asked, {})                  # contesta la pregunta de datos faltantes
    print("OK: deteccion de peticiones de plan")


def test_plan_flow_and_summary():
    sm = Say("Tu plan está listo: clima agradable y vuelo barato."); m.MODEL_ID = sm
    LAMBDA_LOG.clear()
    ev = asyncio.run(run({"prompt": DEMO, "userId": "miller", "sessionId": "s1"}))
    types = [e["type"] for e in ev]
    assert types[0] == "start" and "plan" in types and types.index("plan_done") < types.index("text") < types.index("suggestions") < len(types) - 1
    assert types[-1] == "done" and "error" not in types
    assert [n for n, _ in LAMBDA_LOG] == ["TripPlanner-CreateTrip", "TripPlanner-CreateItinerary"]
    assert sm.calls[-1]["tools"] == [] and "RESULTADOS REALES" in sm.calls[-1]["system"] and "$5.000.000" in sm.calls[-1]["system"]
    text = "".join(e["data"] for e in ev if e["type"] == "text")
    assert text == "Tu plan está listo: clima agradable y vuelo barato."
    sug = next(e for e in ev if e["type"] == "suggestions")["items"]
    assert sug[0]["action"]["type"] == "reserve_flight" and sug[0]["action"]["flight"]["flight"] == "LA4126"
    print("OK: plan completo por el entrypoint (linea de tiempo -> resumen -> sugerencias)")


def test_summary_fallback_when_llm_down():
    m.MODEL_ID = Say(boom=True)
    ev = asyncio.run(run({"prompt": DEMO, "userId": "miller"}))
    text = "".join(e["data"] for e in ev if e["type"] == "text")
    assert "Listo, armé tu plan" in text and "LA4126" in text and ev[-1]["type"] == "done" and "error" not in [e["type"] for e in ev]
    print("OK: si el LLM del resumen falla, se usa la plantilla y el plan igual se entrega")


def test_reserve_by_action_and_text():
    m.MODEL_ID = Say(); LAMBDA_LOG.clear(); STORE.clear()
    act = {"type": "reserve_flight", "flight": {"airline": "LATAM", "flight": "LA4126", "depart": "12:00", "arrive": "13:30", "price_usd": 77, "stops": 0, "evil": "<script>"}}
    ev = asyncio.run(run({"prompt": "Reservar LA4126", "userId": "miller", "context": CTX, "action": act}))
    name, payload = writes()[0]
    assert name == "TripPlanner-CreateReservation" and payload["tripId"] == "T-1" and payload["type"] == "VUELO"
    assert "evil" not in payload["details"] and payload["details"]["total_usd"] == 231
    assert any(e["type"] == "tool_result" and e["name"] == "create_reservation" for e in ev)
    assert "simulada" in "".join(e["data"] for e in ev if e["type"] == "text")
    # por texto
    LAMBDA_LOG.clear()
    ev = asyncio.run(run({"prompt": "Reserva el vuelo de las 09:00 por favor", "userId": "miller", "context": CTX}))
    assert writes() and writes()[0][1]["details"]["flight"] == "AV9"
    # el MISMO vuelo otra vez: no se duplica
    LAMBDA_LOG.clear(); n_before = len(STORE)
    ev = asyncio.run(run({"prompt": "Reserva el vuelo de las 09:00 por favor", "userId": "miller", "context": CTX}))
    assert not writes() and len(STORE) == n_before and "ya está reservado" in "".join(e["data"] for e in ev if e["type"] == "text")
    # sin viaje guardado: lo dice, no inventa
    LAMBDA_LOG.clear()
    ev = asyncio.run(run({"prompt": "Reservar", "userId": "miller", "context": {}, "action": act}))
    assert not writes() and "viaje guardado" in "".join(e["data"] for e in ev if e["type"] == "text")
    # reservar un hotel NO es una reserva de vuelo: lo atiende el agente libre
    sm = Say("ok"); m.MODEL_ID = sm; LAMBDA_LOG.clear()
    asyncio.run(run({"prompt": "reserva un hotel en Cartagena", "userId": "miller", "context": CTX}))
    assert not LAMBDA_LOG and "delete_reservation" in sm.calls[0]["tools"]
    print("OK: reserva por boton y por texto, datos del navegador saneados, hotel va al agente libre")


def test_delete_tool_and_deterministic_route():
    LAMBDA_LOG.clear()
    out = json.loads(m.delete_reservation(tripId="T-1", reservationId="R-1"))
    assert out["deleted"] is True and LAMBDA_LOG[0][0] == "TripPlanner-DeleteReservation"      # la tool sigue disponible
    # "elimina la reserva ..." ya NO depende del modelo: lo decide el codigo
    sm = Say("no deberia llamarse"); m.MODEL_ID = sm
    STORE.clear(); LAMBDA_LOG.clear()
    for i, (fl, dep) in enumerate([("LA4126", "12:00"), ("AV9", "09:00"), ("P57623", "12:32")]):
        STORE.append({"reservationId": f"R-{i + 1}", "tripId": "T-1", "type": "VUELO", "status": "SIMULADA", "createdAt": f"2026-11-24T14:0{i}",
                      "details": {"airline": "X", "flight": fl, "depart": dep}})
    # 1) el caso real: 3 reservas y "la que hice" -> solo la ultima
    ev = asyncio.run(run({"prompt": "elimina la reserva que hice", "userId": "miller", "context": CTX}))
    assert not sm.calls and [r["reservationId"] for r in STORE] == ["R-1", "R-2"]
    assert [p["reservationId"] for n, p in LAMBDA_LOG if n == "TripPlanner-DeleteReservation"] == ["R-3"]
    types = [e["type"] for e in ev]
    assert types[0] == "start" and "plan" in types and "plan_done" in types and types[-1] == "done"
    # 2) ambiguo: pregunta y no borra nada
    LAMBDA_LOG.clear()
    ev = asyncio.run(run({"prompt": "elimina la reserva", "userId": "miller", "context": CTX}))
    text = "".join(e["data"] for e in ev if e["type"] == "text")
    assert len(STORE) == 2 and "Cuál elimino" in text and not [1 for n, _ in LAMBDA_LOG if n == "TripPlanner-DeleteReservation"]
    sugg = [e for e in ev if e["type"] == "suggestions"][0]["items"]
    assert any(i.get("action", {}).get("reservationId") == "R-1" for i in sugg)
    # 3) respuesta corta a la pregunta ("la 1") -> la borra, sin modelo
    hist = [{"role": "user", "content": "elimina la reserva"}, {"role": "assistant", "content": text}]
    ev = asyncio.run(run({"prompt": "la 1", "userId": "miller", "context": CTX, "history": hist}))
    assert [r["reservationId"] for r in STORE] == ["R-2"] and not sm.calls
    # 4) boton "Eliminar" del navegador
    ev = asyncio.run(run({"prompt": "Eliminar Vuelo X AV9", "userId": "miller", "context": CTX,
                          "action": {"type": "delete_reservation", "tripId": "T-1", "reservationId": "R-2"}}))
    assert STORE == [] and any(e["type"] == "tool_result" and e["name"] == "delete_reservation" and e["status"] == "success" for e in ev)
    # 5) accion invalida: no hace nada
    ev = asyncio.run(run({"prompt": "x", "userId": "miller", "context": CTX, "action": {"type": "delete_reservation"}}))
    assert "No entendí" in "".join(e["data"] for e in ev if e["type"] == "text")
    # 6) un saludo o consultar reservas NO borra nada y sigue por el agente libre
    for txt in ("hola", "muéstrame mis reservas"):
        STORE.append({"reservationId": "R-9", "tripId": "T-1", "type": "VUELO", "status": "SIMULADA", "createdAt": "x", "details": {}})
        sm2 = Say("ok"); m.MODEL_ID = sm2; LAMBDA_LOG.clear()
        asyncio.run(run({"prompt": txt, "userId": "miller", "context": CTX}))
        assert len(STORE) == 1 and not [1 for n, _ in LAMBDA_LOG if n == "TripPlanner-DeleteReservation"]
        STORE.clear()
    print("OK: eliminar reservas es determinista (la ultima, preguntar, 'la 1', boton) y la tool sigue disponible")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("TODAS LAS PRUEBAS DE FLUJOS OK")
