"""Pruebas del flujo plan-and-execute (planner.py) sin AWS: interpretacion, itinerario, linea de tiempo y fallos.
Ejecutar desde la raiz del proyecto:   python tests/test_planner.py      (o:  python -m pytest tests/test_planner.py -q)
"""
import asyncio
import json
import pathlib
import re
import sys
from datetime import date

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(next(p for p in (ROOT / "app/OrchestratorAgent", ROOT / "OrchestratorAgent") if p.exists())))
import planner as P  # noqa: E402

TODAY = date(2026, 11, 24)
DEMO = ("Quiero organizar unas vacaciones de cuatro días en Cartagena. Salgo desde Bogotá, viajo con dos personas más y "
        "tengo un presupuesto de 5 millones de pesos. Quiero conocer el clima, encontrar vuelos y organizar algunas actividades turísticas.")


def test_parsers():
    cases_budget = {"5 millones de pesos": 5_000_000, "1.000.000 de pesos": 1_000_000, "$5.000.000": 5_000_000, "800 mil pesos": 800_000,
                    "medio millon": 500_000, "dos millones y medio": 2_500_000, "2,5 millones": 2_500_000, "tengo 3000000": 3_000_000,
                    "salgo el 04/12/2026 por 4 dias": None, "para 3 personas": None}
    for txt, want in cases_budget.items():
        assert P.parse_budget(txt) == want, (txt, P.parse_budget(txt))
    assert P.parse_budget("antes 2 millones, mejor 3 millones") == 3_000_000            # gana el ultimo
    trav = {"viajo con dos personas mas": 3, "somos 4": 4, "vamos 2 personas": 2, "para 5 personas": 5, "voy solo": 1,
            "mi esposa y yo": 2, "somos tres": 3, "quiero ir a cartagena": None}
    for txt, want in trav.items():
        assert P.parse_travelers(P.norm(txt)) == want, (txt, P.parse_travelers(P.norm(txt)))
    days = {"vacaciones de cuatro dias": 4, "para 2 dias": 2, "3 noches": 4, "una semana": 7, "fin de semana": 3,
            "salgo en 10 dias": None, "quiero viajar": None}
    for txt, want in days.items():
        assert P.parse_days(P.norm(txt)) == want, (txt, P.parse_days(P.norm(txt)))
    print("OK: presupuesto, viajeros y dias")


def test_cities():
    cases = {
        DEMO: ("Bogotá", "Cartagena"),
        "quiero irme de vacaciones 4 dias a santa marta": (None, "Santa Marta"),
        "quiero salir de barranquilla atlantico, vamos 2 personas": ("Barranquilla", None),
        "de Medellin a San Andres": ("Medellín", "San Andrés"),
        "vuelos desde Cali hacia Cartagena de Indias": ("Cali", "Cartagena"),
        "quiero ir a Cartagena saliendo de Bogota": ("Bogotá", "Cartagena"),
    }
    for txt, want in cases.items():
        assert P.det_cities(txt) == want, (txt, P.det_cities(txt))
    print("OK: ciudades de origen y destino")


def test_extract_demo_without_llm():
    req, missing, how = P.extract_request(DEMO, TODAY, llm_text=None)
    assert req and not missing and how == "reglas"
    assert (req.origin, req.destination, req.days, req.travelers, req.budget_cop) == ("Bogotá", "Cartagena", 4, 3, 5_000_000)
    assert req.start == date(2026, 12, 4) and req.end == date(2026, 12, 7)
    assert any("salida" in a for a in req.assumptions)
    print("OK: el escenario obligatorio se entiende solo con reglas:", req.interpretation())


def test_extract_slot_filling_and_llm():
    text = "quiero irme de vacaciones 4 dias a santa marta . quiero salir de barranquilla atlantico, vamos 2 personas y tenemos 1.000.000 de pesos para 2 dias"
    req, missing, _ = P.extract_request(text, TODAY)
    assert req and (req.origin, req.destination, req.travelers, req.budget_cop, req.days) == ("Barranquilla", "Santa Marta", 2, 1_000_000, 2)

    calls = []

    def fake_llm(system, prompt):
        calls.append(prompt)
        return '<thinking>x</thinking>```json\n{"origin": "Quito", "destination": "Cartagena", "days": null, "travelers": null, "budget_cop": null, "start_date": "2026-12-10", "end_date": "2026-12-14"}\n```'
    req, missing, how = P.extract_request("Quiero ir a Cartagena desde Quito, somos 2, tengo 3 millones, del 10 al 14 de diciembre", TODAY, fake_llm)
    assert calls and req and how == "reglas + Nova Pro"
    assert (req.origin, req.start, req.end, req.days) == ("Quito", date(2026, 12, 10), date(2026, 12, 14), 5), req.interpretation()

    # el LLM alucina una ciudad que el usuario nunca dijo -> se descarta y se pide el dato
    req, missing, _ = P.extract_request("quiero viajar a Cartagena con 3 millones, somos 2",
                                        TODAY, lambda s, p: '{"origin": "Tokio", "destination": "Cartagena"}')
    assert req is None and missing == ["la ciudad de salida"], missing
    # sin presupuesto ni personas
    req, missing, _ = P.extract_request("quiero ir de Cali a Cartagena", TODAY)
    assert req is None and "tu presupuesto en pesos" in missing and "cuántas personas viajan" in missing
    # "decide tu": usa valores por defecto y los declara
    req, missing, _ = P.extract_request("quiero ir a Cartagena, tu decides", TODAY, allow_defaults=True)
    assert req and req.origin == "Bogotá" and req.travelers == 1 and len([a for a in req.assumptions if a.startswith("Asum")]) >= 4
    # origen = destino
    req, missing, _ = P.extract_request("de Cali a Cali somos 2 con 3 millones", TODAY)
    assert req is None
    # LLM caido: las reglas siguen funcionando con el escenario obligatorio
    def boom(s, p):
        raise RuntimeError("sin red")
    req, _, _ = P.extract_request(DEMO + " el 10 de diciembre", TODAY, boom)
    assert req and req.destination == "Cartagena"
    print("OK: relleno de datos, LLM como apoyo, anti-alucinacion y valores por defecto")


def _req(dest="Cartagena", days=4):
    r, _, _ = P.extract_request(f"vacaciones de {days} dias en {dest} desde Bogota somos 3 con 5 millones", TODAY)
    return r


def test_itinerary_catalog_and_validation():
    req = _req()
    flight = {"airline": "LATAM", "flight": "LA4126", "depart": "12:00", "price_usd": 77}
    days, src = P.build_itinerary(req, flight, None)                    # sin LLM: solo catalogo
    assert src == "lugares reales del destino" and [d["day"] for d in days] == [1, 2, 3, 4]
    assert "LA4126" in days[0]["activities"][0] and days[-1]["activities"][-1].startswith("Check-out y regreso a Bogotá")
    flat = " ".join(a for d in days for a in d["activities"])
    for c, places in P.PLACES.items():
        if c != "Cartagena":
            assert not any(P._short(p) in flat for p in places), c            # nunca lugares de otra ciudad
    # propuesta del LLM: valida indices, repeticiones y titulos con lugares de otra ciudad
    good = {"days": [{"day": 1, "title": "Llegada y atardecer", "places": [1, 3]}, {"day": 2, "title": "Castillo e historia", "places": [2, 8, 9]},
                     {"day": 3, "title": "Islas", "places": [5, 6, 4]}, {"day": 4, "title": "Museo del Oro Tairona", "places": [10]}]}
    days, src = P.build_itinerary(req, flight, good)
    assert src == "lugares reales del destino" and days[0]["title"] == "Llegada y atardecer"
    assert days[3]["title"] == "Despedida y regreso"                               # titulo con sitio de Santa Marta -> descartado
    assert "Castillo San Felipe de Barajas" in " ".join(days[1]["activities"])
    bad = {"days": [{"day": 1, "places": [99, 1, 1]}, "basura", {"day": 9, "places": [2]}]}
    days, _ = P.build_itinerary(req, flight, bad)
    assert len(days) == 4 and all(len(d["activities"]) >= 3 for d in days)
    # destino fuera del catalogo: actividades genericas y sin nombres inventados
    r2 = P.extract_request("vacaciones de 3 dias en Manizales desde Bogota somos 2 con 2 millones", TODAY)[0]
    days, _ = P.build_itinerary(r2, None, None)
    assert len(days) == 3 and "Manizales" in days[0]["activities"][0]
    d1, _ = P.build_itinerary(_req(days=1), flight, None)
    assert len(d1) == 1 and d1[0]["activities"][-1].startswith("Regreso")
    long, _ = P.build_itinerary(_req(days=9), flight, None)                         # mas dias que lugares: rellena sin romper
    assert len(long) == 9
    print("OK: itinerario con lugares reales, validacion de la propuesta del LLM y respaldo")


# ---------------- linea de tiempo completa con dependencias simuladas ----------------
WEATHER = {"city": "Cartagena", "source": "pronostico", "days": [
    {"date": "2026-12-04", "condition": "Parcialmente nublado", "temp_min_c": 26, "temp_max_c": 31, "rain_probability_pct": 20},
    {"date": "2026-12-05", "condition": "Lluvia ligera", "temp_min_c": 25, "temp_max_c": 30, "rain_probability_pct": 70}]}
FLIGHTS = {"flights": [
    {"airline": "Avianca", "flight": "AV9", "depart": "09:00", "arrive": "10:30", "price_usd": 120, "stops": 1},
    {"airline": "LATAM", "flight": "LA4126", "depart": "12:00", "arrive": "13:30", "price_usd": 77, "stops": 0}]}


def make_deps(weather=None, flights=None, lam=None, llm=None):
    log = []

    def lam_default(name, payload):
        log.append((name, payload))
        if name == "TripPlanner-CreateTrip":
            return {"tripId": "T-1", **payload, "status": "PLANEANDO"}
        if name == "TripPlanner-CreateItinerary":
            return {"itineraryId": "I-1", **payload}
        if name == "TripPlanner-CreateReservation":
            return {"reservationId": "R-1", "status": "SIMULADA", **payload}
        return {"error": "?"}
    deps = P.Deps(flights or (lambda o, d, dt: FLIGHTS), weather or (lambda c, a, b: WEATHER), lam or lam_default,
                  llm or (lambda s, p: '{"days": []}'))
    deps.log = log
    return deps


async def collect(gen):
    return [ev async for ev in gen]


def run(text, deps, state=None, **kw):
    state = {"userId": "miller"} if state is None else state
    return asyncio.run(collect(P.run_plan(text, deps, state, TODAY, **kw))), state


def steps_of(events):
    """Ultimo estado de cada paso."""
    out = {}
    for e in events:
        if e["type"] == "step":
            out[e["id"]] = e
    return out


def test_full_plan_timeline():
    deps = make_deps()
    ev, st = run(DEMO, deps)
    types = [e["type"] for e in ev]
    assert types[0] == "plan" and types[-1] == "plan_done" and ev[-1]["ok"] is True
    steps = steps_of(ev)
    assert [s for s in steps] == ["understand", "weather", "flights", "budget", "itinerary", "save_trip", "save_itinerary"]
    assert all(s["status"] == "done" for s in steps.values()), {k: v["status"] for k, v in steps.items()}
    assert steps["understand"]["title"] == "Bogotá → Cartagena" and "3 viajeros" in steps["understand"]["detail"]
    # cada tool_start tiene su tool_result y apunta al paso de la linea de tiempo
    starts = {e["id"]: e for e in ev if e["type"] == "tool_start"}
    results = {e["id"]: e for e in ev if e["type"] == "tool_result"}
    assert set(starts) == set(results) and all(e["step"] for e in starts.values())
    assert [e["name"] for e in ev if e["type"] == "tool_result"].count("create_trip") == 1
    by_name = {e["name"]: e for e in results.values()}
    assert by_name["ask_flights"]["data"]["flights"][0]["flight"] == "LA4126"          # ordenados por precio
    assert by_name["calculate_budget"]["data"]["travelers"] == 3 and by_name["calculate_budget"]["data"]["budgetCOP"] == 5_000_000
    assert by_name["create_trip"]["data"]["travelers"] == 3 and by_name["create_trip"]["data"]["budget"] == 5_000_000
    assert by_name["create_trip"]["data"]["userId"] == "miller"
    assert by_name["create_itinerary"]["data"]["tripId"] == "T-1" and len(by_name["create_itinerary"]["data"]["days"]) == 4
    assert [n for n, _ in deps.log] == ["TripPlanner-CreateTrip", "TripPlanner-CreateItinerary"]
    # clima y vuelos arrancan juntos (paralelo)
    first_running = [e["id"] for e in ev if e["type"] == "step" and e["status"] == "running"]
    assert first_running[:2] == ["weather", "flights"]
    kinds = [(e["type"], e.get("id")) for e in ev if e["type"] in ("plan", "step")][:3]
    assert kinds[0][0] == "plan" and kinds[1] == ("step", "understand")                     # la linea de tiempo nace ya con la solicitud entendida
    facts = " ".join(st["facts"])
    assert "Bogotá → Cartagena" in facts and "LA4126" in facts and "$5.000.000" in facts and "guardados" in facts
    assert st["suggestions"][0]["action"]["type"] == "reserve_flight" and st["suggestions"][0]["action"]["flight"]["flight"] == "LA4126"
    assert "reserve el vuelo más barato" in P.template_summary(st["facts"])
    print("OK: linea de tiempo completa, en orden, con tarjetas, guardado y sugerencias")


def test_partial_failures_do_not_break_the_plan():
    # clima caido (429): el plan sigue y lo dice
    deps = make_deps(weather=lambda c, a, b: {"error": "HTTP 429 Daily API request limit exceeded"})
    ev, st = run(DEMO, deps)
    steps = steps_of(ev)
    assert steps["weather"]["status"] == "error" and "saturado" in steps["weather"]["detail"] and "retry" in steps["weather"]
    assert steps["weather"]["attempt"] == 2 and "429" not in json.dumps(ev, ensure_ascii=False)          # no se filtran detalles internos
    assert steps["flights"]["status"] == "done" and steps["save_trip"]["status"] == "done" and ev[-1]["ok"] is False
    assert "Clima: NO estuvo disponible" in " ".join(st["facts"])
    # vuelos caidos: presupuesto sin vuelos y nota
    deps = make_deps(flights=lambda o, d, dt: {"error": "timeout"})
    ev, st = run(DEMO, deps)
    bud = next(e for e in ev if e["type"] == "tool_result" and e["name"] == "calculate_budget")["data"]
    assert bud["breakdown"][0]["costCOP"] == 0 and "vuelos" in bud["note"]
    assert not any(s["action"] for s in st["suggestions"] if "action" in s)                       # sin vuelo no se ofrece reservar
    # sin resultados de vuelos (lista vacia)
    ev, st = run(DEMO, make_deps(flights=lambda o, d, dt: {"flights": []}))
    assert "no se encontraron opciones" in " ".join(st["facts"])
    # excepcion en un especialista
    def explode(*a):
        raise ConnectionError("boom")
    ev, st = run(DEMO, make_deps(flights=explode))
    assert steps_of(ev)["flights"]["status"] == "error"
    # create_trip falla: no se intenta guardar el itinerario
    def lam(name, payload):
        return {"error": "Faltan campos"} if name == "TripPlanner-CreateTrip" else {"itineraryId": "I"}
    deps = make_deps(lam=lam)
    ev, st = run(DEMO, deps)
    steps = steps_of(ev)
    assert steps["save_trip"]["status"] == "error" and steps["save_itinerary"]["status"] == "skipped" and "retry" in steps["save_trip"]
    assert "NO se pudo guardar" in " ".join(st["facts"]) and not any("action" in s for s in st["suggestions"])
    # las escrituras no se reintentan (no duplicar viajes)
    calls = []
    def lam2(name, payload):
        calls.append(name)
        raise TimeoutError("lento")
    run(DEMO, make_deps(lam=lam2))
    assert calls == ["TripPlanner-CreateTrip"], calls
    print("OK: fallos parciales (clima, vuelos, guardado) no rompen el plan y no filtran detalles internos")


def test_missing_data_and_retry_success():
    ev, st = run("quiero ir de Cali a Cartagena", make_deps())
    assert st["missing"] and not any(e["type"] in ("plan", "step", "plan_done", "tool_start") for e in ev)     # solo se pregunta: sin linea de tiempo en rojo
    # el especialista falla una vez y acierta en el reintento
    n = {"c": 0}
    def flaky(c, a, b):
        n["c"] += 1
        return {"error": "tmp"} if n["c"] == 1 else WEATHER
    ev, st = run(DEMO, make_deps(weather=flaky))
    s = steps_of(ev)["weather"]
    assert s["status"] == "done" and s["attempt"] == 2
    print("OK: faltan datos -> no se llama a nadie; reintento automatico del especialista")


def test_llm_itinerary_is_used_when_valid():
    seen = []
    def llm(system, prompt):
        seen.append(prompt)
        return json.dumps({"days": [{"day": 1, "title": "Llegada y Getsemaní", "places": [3, 1]}, {"day": 2, "title": "Fuertes y museos", "places": [2, 8, 9]},
                                    {"day": 3, "title": "Playa y mar", "places": [5, 6, 11]}, {"day": 4, "title": "Ultimo paseo", "places": [10]}]})
    ev, st = run(DEMO, make_deps(llm=llm))
    assert seen and "Lluvia ligera" in seen[0] and "LUGARES DISPONIBLES" in seen[0]               # el LLM recibe clima y catalogo
    days = next(e for e in ev if e["type"] == "tool_result" and e["name"] == "plan_itinerary")["data"]
    assert days["source"] == "lugares reales del destino" and days["days"][0]["title"] == "Llegada y Getsemaní"
    print("OK: el LLM recibe clima y catalogo y su propuesta valida se respeta")


def test_reserve_flow_and_pick():
    opts = FLIGHTS["flights"]
    assert P.pick_flight("reserva el vuelo LA4126", opts)["flight"] == "LA4126"
    assert P.pick_flight("reserva el de las 09:00", opts)["flight"] == "AV9"
    assert P.pick_flight("reserva el vuelo mas barato", opts)["flight"] == "LA4126"
    assert P.pick_flight("reserva", []) is None
    deps = make_deps()
    state = {}
    trip = {"tripId": "T-1", "origin": "Bogotá", "destination": "Cartagena", "startDate": "04/12/2026", "travelers": 3}
    ev = asyncio.run(collect(P.run_reserve(opts[1], trip, deps, state)))
    assert state["ok"] and "simulada" in state["text"] and "US$231" in state["text"]
    name, payload = [x for x in deps.log if x[0] == "TripPlanner-CreateReservation"][0]
    assert payload["type"] == "VUELO" and payload["details"]["total_usd"] == 231
    assert payload["details"]["flight"] == "LA4126" and "card" not in json.dumps(payload).lower()
    assert [s["status"] for s in steps_of(ev).values()] == ["done", "done"] and ev[-1]["ok"]
    bad = make_deps(lam=lambda n, p: {"error": "x"})
    st2 = {}
    ev = asyncio.run(collect(P.run_reserve(opts[1], trip, bad, st2)))
    assert not st2["ok"] and steps_of(ev)["reserve"]["status"] == "error" and "retry" in steps_of(ev)["reserve"]
    print("OK: reserva de vuelo determinista con su linea de tiempo")


def _res(rid, flight, depart, created, status="SIMULADA", airline="LATAM", tid="T-1", typ="VUELO", **extra):
    d = {"airline": airline, "flight": flight, "depart": depart, "arrive": "20:00"} if typ == "VUELO" else dict(extra)
    return {"reservationId": rid, "tripId": tid, "type": typ, "status": status, "details": d, "createdAt": created}


R3 = [_res("R-1", "LA4126", "12:00", "2026-11-24T14:00"), _res("R-2", "AV9", "09:00", "2026-11-24T14:05", airline="Avianca"),
      _res("R-3", "P57623", "12:32", "2026-11-24T14:10", airline="Wingo airlines")]


def test_reserve_does_not_duplicate():
    """Reservar dos veces el mismo vuelo: la segunda no crea nada y lo explica."""
    opts = FLIGHTS["flights"]
    trip = {"tripId": "T-1", "origin": "Bogotá", "destination": "Cartagena", "startDate": "04/12/2026", "travelers": 3}
    existing = [_res("R-1", opts[1]["flight"], opts[1]["depart"], "2026-11-24T14:00", airline=opts[1]["airline"])]
    existing[0]["details"]["arrive"] = opts[1]["arrive"]
    log = []

    def lam(name, payload):
        log.append(name)
        return {"reservations": existing} if name == "TripPlanner-GetReservations" else {"reservationId": "R-9", **payload}
    state = {}
    ev = asyncio.run(collect(P.run_reserve(opts[1], trip, P.Deps(None, None, lam, None), state)))
    assert "TripPlanner-CreateReservation" not in log, log
    st = steps_of(ev)
    assert st["pick"]["status"] == "done" and st["reserve"]["status"] == "skipped" and "ya existe" in st["reserve"]["detail"].lower()
    assert state["ok"] and "ya está reservado" in state["text"] and "Simulada" in state["text"] and ev[-1]["type"] == "plan_done"
    # otro vuelo distinto SI se reserva
    log.clear(); state = {}
    asyncio.run(collect(P.run_reserve(opts[0], trip, P.Deps(None, None, lam, None), state)))
    assert "TripPlanner-CreateReservation" in log and state["ok"]
    # si no se puede consultar la lista, no se bloquea la reserva
    log.clear(); state = {}
    asyncio.run(collect(P.run_reserve(opts[1], trip, P.Deps(None, None, lambda n, p_: (log.append(n) or {"error": "x"}) if n == "TripPlanner-GetReservations" else (log.append(n) or {"reservationId": "R-9"}), None), state)))
    assert "TripPlanner-CreateReservation" in log
    print("OK: no se duplica el mismo vuelo (otro vuelo o lista inaccesible si reserva)")


def test_choose_reservations():
    """'Elimina la reserva que hice' con 3 reservas borraba TODAS: ahora la ultima; ante la duda, pregunta."""
    c = P.choose_reservations
    assert c("elimina la reserva que hice", R3) == ("one", R3[2])                       # la ultima, no todas
    assert c("borra la ultima reserva", R3) == ("one", R3[2])
    assert c("elimina la reserva", R3)[0] == "ask"                                       # varias y sin pista: pregunta
    assert c("elimina todas las reservas", R3) == ("all", R3)
    assert c("elimina la reserva 2", R3) == ("one", R3[1]) and c("la 1", R3) == ("one", R3[0]) and c("5", R3)[0] == "ask"
    assert c("elimina la reserva de Avianca", R3) == ("one", R3[1])
    assert c("elimina la reserva del vuelo LA4126", R3) == ("one", R3[0])
    assert c("elimina la reserva de las 12:32", R3) == ("one", R3[2])                  # la hora no se confunde con un numero
    assert c("elimina la reserva del hotel", R3)[0] == "nomatch"
    assert c("elimina la reserva", R3[:1]) == ("one", R3[0])                            # si solo hay una, esa
    assert c("elimina la reserva", [])[0] == "none"
    mix = R3[:2] + [_res("R-4", None, None, "2026-11-24T14:20", typ="HOTEL", name="Hotel Caribe")]
    assert c("elimina la reserva del hotel", mix) == ("one", mix[2])
    assert c("elimina la reserva del vuelo", mix)[0] == "ask"
    assert P.res_label(R3[0]) == "Vuelo LATAM LA4126 (12:00)" and "Hotel Caribe" in P.res_label(mix[2])
    print("OK: eliminar reservas: la ultima, por numero, por texto, todas o preguntar")


def _delete_deps(store, fail_delete=False):
    log = []

    def lam(name, payload):
        log.append((name, payload))
        if name == "TripPlanner-GetTrips":
            return {"trips": [{"tripId": "T-1", "origin": "Bogotá", "destination": "Cartagena", "createdAt": "2026-11-24"}]}
        if name == "TripPlanner-GetReservations":
            return {"reservations": [dict(r) for r in store if r["tripId"] == payload["tripId"]]}
        if name == "TripPlanner-DeleteReservation":
            if fail_delete:
                return {"error": "boom"}
            store[:] = [r for r in store if r["reservationId"] != payload["reservationId"]]
            return {"deleted": True, **payload}
        return {"error": "?"}
    deps = P.Deps(None, None, lam, None)
    deps.log = log
    return deps


def _del(prompt, store, trip=None, sel=None, fail=False):
    deps = _delete_deps(store, fail)
    state = {}
    ev = asyncio.run(collect(P.run_delete(prompt, deps, state, trip=trip, user_id="miller", sel=sel)))
    return ev, state, deps


def test_delete_flow():
    # el caso real: 3 reservas y "la que hice" -> solo la ultima
    store = [dict(r) for r in R3]
    ev, st, deps = _del("elimina la reserva que hice", store, trip={"tripId": "T-1"})
    dels = [p_ for n, p_ in deps.log if n == "TripPlanner-DeleteReservation"]
    assert [d["reservationId"] for d in dels] == ["R-3"] and [r["reservationId"] for r in store] == ["R-1", "R-2"]
    assert st["ok"] and "Wingo" in st["text"] and "Te quedan 2" in st["text"]
    s = steps_of(ev)
    assert s["find"]["status"] == "done" and s["delete"]["status"] == "done" and ev[-1]["type"] == "plan_done" and ev[-1]["ok"]
    assert any(e["type"] == "tool_result" and e["name"] == "delete_reservation" and e["data"]["reservationId"] == "R-3" for e in ev)
    # ambiguo: pregunta, NO borra, y ofrece botones con accion
    store = [dict(r) for r in R3]
    ev, st, deps = _del("elimina la reserva", store, trip={"tripId": "T-1"})
    assert not [1 for n, _ in deps.log if n == "TripPlanner-DeleteReservation"] and len(store) == 3
    assert "cual elimino" in P.norm(st["text"]) and "1." in st["text"] and steps_of(ev)["delete"]["status"] == "skipped"
    acts = [x["action"] for x in st["suggestions"] if x.get("action")]
    assert {"type": "delete_reservation", "tripId": "T-1", "reservationId": "R-2"} in acts and any(a.get("all") for a in acts)
    # boton: una concreta
    store = [dict(r) for r in R3]
    ev, st, deps = _del("Eliminar Vuelo Avianca AV9", store, trip={"tripId": "T-1"}, sel={"reservationId": "R-2"})
    assert [r["reservationId"] for r in store] == ["R-1", "R-3"] and st["ok"]
    # boton: todas
    ev, st, deps = _del("Eliminar todas", store, trip={"tripId": "T-1"}, sel={"all": True})
    assert store == [] and "2 reservas" in st["text"] and "Ya no tienes" in st["text"]
    # sin reservas
    ev, st, deps = _del("elimina la reserva", [], trip={"tripId": "T-1"})
    assert st["ok"] and "No tienes reservas" in st["text"]
    # sin viaje activo: busca en los viajes del usuario
    store = [dict(R3[0])]
    ev, st, deps = _del("elimina la reserva", store)
    assert [n for n, _ in deps.log][:2] == ["TripPlanner-GetTrips", "TripPlanner-GetReservations"] and store == []
    # una reserva que ya no existe (boton viejo)
    ev, st, deps = _del("Eliminar", [dict(R3[0])], trip={"tripId": "T-1"}, sel={"reservationId": "R-99"})
    assert st["ok"] and "ya no existe" in st["text"]
    # fallo al borrar: error con Reintentar, sin afirmar que borro
    store = [dict(R3[0])]
    ev, st, deps = _del("elimina la reserva", store, trip={"tripId": "T-1"}, fail=True)
    s = steps_of(ev)
    assert not st["ok"] and s["delete"]["status"] == "error" and s["delete"].get("retry") and "No pude eliminar" in st["text"] and len(store) == 1
    print("OK: eliminar reservas (ultima, ambiguo, botones, todas, vacio, fallo)")


def test_country_asks_for_city_and_international_cities():
    """'Brasil' es un pais: no tiene clima, vuelos ni itinerario propios. Se pide una ciudad (y 'Rio de Janeiro' se entiende)."""
    t = "quiero viajar a brasil desde barranquilla, somos 2 personas y tenemos 10.000.000 de pesos"
    req, missing, _ = P.extract_request(t, TODAY)
    assert req is None and len(missing) == 1 and "Brasil" in missing[0] and "Río de Janeiro" in missing[0], missing
    llm_country = lambda s_, p_: '{"origin": "Barranquilla", "destination": "Brasil", "travelers": 2, "budget_cop": 10000000}'
    req, missing, _ = P.extract_request(t, TODAY, llm_country)                       # el LLM tambien devuelve el pais
    assert req is None and "ciudad de destino en Brasil" in missing[0], missing
    ev, st = run(t, make_deps())
    assert st["missing"] and not any(e["type"] in ("plan", "step", "tool_start") for e in ev)
    # la respuesta con la ciudad completa el pedido
    req, missing, _ = P.extract_request(t + " . rio de janeiro", TODAY)
    assert req and req.destination == "Río de Janeiro" and not missing, (req and req.destination, missing)
    assert P.det_cities("de Bogota a São Paulo")[1] == "São Paulo" and P.det_cities("quiero ver el rio Amazonas desde Leticia")[1] != "Río de Janeiro"
    for pais in ("Mexico", "España", "Estados Unidos", "Perú"):
        r_, m_, _ = P.extract_request(f"quiero ir a {pais} desde Cali, somos 2 con 5 millones", TODAY)
        assert r_ is None and "ciudad de destino" in m_[0], (pais, m_)
    print("OK: un pais pide una ciudad concreta y las ciudades internacionales frecuentes se entienden")


def test_international_catalog_and_generic_itinerary():
    # destinos internacionales frecuentes: lugares reales del catalogo, no genericos
    for city, must in (("Punta Cana", "Bávaro"), ("Río de Janeiro", "Cristo Redentor"), ("Nueva York", "Central Park"), ("París", "Torre Eiffel")):
        r_, m_, _ = P.extract_request(f"quiero ir de Barranquilla a {city} 4 dias, somos 2 con 10 millones", TODAY)
        assert r_ and r_.destination == city, (city, m_)
        d_, s_ = P.build_itinerary(r_, None, None)
        assert s_ == "lugares reales del destino" and must in " ".join(a for d in d_ for a in d["activities"]), city
    test_generic_itinerary_for_unlisted_city()
    print("OK: destinos internacionales con lugares reales")


def test_generic_itinerary_for_unlisted_city():
    req, _, _ = P.extract_request("quiero ir de Barranquilla a Quito 4 dias, somos 2 con 10 millones", TODAY)
    days, src = P.build_itinerary(req, {"airline": "Avianca", "flight": "AV1", "depart": "10:20"}, None)
    assert src == "actividades generales" and len(days) == 4
    texts = " | ".join(a for d in days for a in d["activities"])
    assert "Tiempo libre" not in texts and "repetir tu lugar favorito" not in texts, texts        # sin relleno repetido
    acts = [a.split(": ", 1)[-1] for d in days for a in d["activities"] if a.startswith(("Mañana", "Tarde", "Atardecer", "Noche"))]
    assert len(acts) == len(set(acts)), acts                                                       # no repite actividades
    assert days[0]["title"] == "Llegada a Quito" and days[-1]["title"] == "Despedida y regreso"
    assert 3 <= len(days[1]["title"]) <= 40 and days[1]["title"] != P.build_itinerary(req, None, None)[0][2]["title"]
    print("OK: itinerario general sin relleno repetido para ciudades fuera del catalogo")


def test_no_jargon_for_users():
    """El usuario no debe leer A2A, MCP, Lambda, DynamoDB, Tool, Orchestrator... Eso vive en el campo 'tech' (opcional)."""
    BAD = re.compile(r"\b(a2a|mcp|lambda|dynamodb|tool|tools|orchestrator|nova|determinista|agent|create_\w+|get_\w+)\b")
    bad = None
    for group in (P.STEPS, P._PICK_STEPS, P._DEL_STEPS):
        for st_ in group:
            shown = P.norm(st_["label"] + " " + st_["agent"])
            assert not BAD.search(shown), st_
            assert st_["tech"] and "via" not in st_
    deps = make_deps()
    ev, st = run(DEMO, deps)
    shown = []
    for e in ev:
        if e["type"] == "step":
            shown += [e.get("detail") or "", e.get("label") or ""] + [str(a) for a in e.get("assumptions") or []]
        if e["type"] == "plan":
            shown += [x["label"] + " " + x["agent"] for x in e["steps"]]
    low = " ".join(shown).lower()
    assert not BAD.search(low), low
    assert not any("via" in e for e in ev if e["type"] == "step")
    # reservar y eliminar tambien
    ev2 = asyncio.run(collect(P.run_reserve(FLIGHTS["flights"][1], {"tripId": "T-1", "origin": "A", "destination": "B"}, make_deps(), {})))
    ev3 = asyncio.run(collect(P.run_delete("elimina la reserva", _delete_deps([dict(R3[0])]), {}, trip={"tripId": "T-1"}, user_id="m")))
    low2 = " ".join((e.get("detail") or "") for e in ev2 + ev3 if e["type"] == "step").lower()
    assert not BAD.search(low2), low2
    print("OK: sin jerga tecnica en lo que ve el usuario (el detalle va aparte, en 'tech')")


def test_night_only_places_never_in_the_morning_or_last_day():
    """Chat real (Cali): el LLM puso 'Una noche de salsa' el ultimo dia en la manana y el dia se llamo 'Noche de salsa'."""
    req, _, _ = P.extract_request("quiero viajar de barranquilla a cali, salgo de barranquilla, somos 2 personas y tenemos 3 millones", TODAY)
    assign = {"days": [{"day": 1, "title": "Visita a lugares icónicos", "places": [1, 8]}, {"day": 2, "title": "Exploración cultural", "places": [2, 5, 4]},
                       {"day": 3, "title": "Naturaleza y diversión", "places": [7, 6, 3]}, {"day": 4, "title": "Noche de salsa", "places": [9]}]}
    days, _ = P.build_itinerary(req, {"airline": "LATAM Airlines", "flight": "LA4151", "depart": "14:20"}, assign)
    assert days[-1]["title"] == "Despedida y regreso" and not any("salsa" in a for a in days[-1]["activities"])
    salsa = [a for d in days for a in d["activities"] if "salsa" in a]
    assert salsa == ["Noche: Una noche de salsa"], salsa                              # solo de noche y en un dia intermedio
    for d in days:
        assert not any(a.startswith(("Mañana", "Tarde")) and P._NIGHT_ONLY.search(a) for a in d["activities"]), d
    for n in (1, 2, 3, 4, 6, 9):                                                      # nunca falla, con o sin LLM
        r_, _, _ = P.extract_request(f"quiero viajar de barranquilla a cali {n} dias, salgo de barranquilla, somos 2 personas y tenemos 3 millones", TODAY)
        d_, _ = P.build_itinerary(r_, None, None)
        assert len(d_) == n and (n == 1 or d_[-1]["title"] == "Despedida y regreso")
        assert not any(a.startswith(("Mañana", "Tarde")) and P._NIGHT_ONLY.search(a) for d in d_ for a in d["activities"])
    print("OK: los planes solo nocturnos van de noche en un dia intermedio y el ultimo dia siempre es la despedida")


def test_parse_changes_and_apply():
    trip = {"tripId": "T-1", "origin": "Barranquilla", "destination": "Cali", "startDate": "04/12/2026", "endDate": "07/12/2026", "travelers": 2, "budget": 3000000}
    cur = P.request_from_trip(trip)
    assert cur.days == 4 and cur.travelers == 2 and cur.budget_cop == 3000000 and cur.destination == "Cali"
    ok = {
        "quiero cambiar la cantidad de dias, el presupuesto se me pasa, quiero que sean 3 dias nada mas": {"days": 3},
        "cambia el presupuesto a 4 millones": {"budget": 4000000},
        "somos 3 personas ahora": {"travelers": 3},
        "agrega 2 personas": {"travelers": 4},
        "alarga el viaje 2 dias": {"days": 6},
        "acorta el viaje a 2 dias": {"days": 2},
        "dos dias mas": None,                                   # sin verbo ni contexto: no es un cambio claro
        "sube el presupuesto en 500 mil": {"budget": 3500000},
        "mejor 5 días": {"days": 5},
        "ahora somos 4 y tenemos 6 millones": {"travelers": 4, "budget": 6000000},
        "cambia el destino a Medellín": {"destination": "Medellín"},
        "quiero salir el 15 de diciembre": {"start": date(2026, 12, 15)},
        "cambia las fechas del 10 al 13 de diciembre": {"start": date(2026, 12, 10), "end": date(2026, 12, 13)},
    }
    for text, want in ok.items():
        r_ = P.parse_changes(text, cur, TODAY)
        if want is None:
            assert r_ is None, (text, r_)
        else:
            assert r_ and r_["changes"] == want and not r_["ask"], (text, r_)
    # no son cambios: consultas, otras acciones o ciudades nombradas de pasada
    for text in ("muéstrame solo vuelos baratos para 2 personas", "cuánto cuesta un hotel para 3 personas", "agrega una visita a Medellín",
                 "agrega una actividad de buceo al día 2", "reserva el vuelo más barato", "¿cuáles son mis viajes guardados?", "quiero 3 dias"):
        assert P.parse_changes(text, cur, TODAY) is None, text
    # pide cambiar sin valor -> pregunta (y la respuesta corta ya cuenta como cambio)
    r_ = P.parse_changes("quiero cambiar la cantidad de dias", cur, TODAY)
    assert r_ and not r_["changes"] and P.UPDATE_ASK_MARKER in P.norm(r_["ask"]) and "4 días" in r_["ask"]
    assert P.parse_changes("3 dias", cur, TODAY, followup=True)["changes"] == {"days": 3} and P.parse_changes("3 dias", cur, TODAY) is None
    assert "ciudad concreta en Brasil" in P.parse_changes("cambia el destino a Brasil", cur, TODAY)["ask"]
    # aplicar
    new, notes, err, same = P.apply_changes(cur, {"days": 3}, TODAY)
    assert new.days == 3 and new.end == date(2026, 12, 6) and notes == ["Duración: 4 → 3 días"] and not err
    new, notes, err, same = P.apply_changes(cur, {"days": 4}, TODAY)
    assert not notes and same == ["4 días"]
    new, notes, err, _ = P.apply_changes(cur, {"start": date(2026, 12, 10)}, TODAY)
    assert new.start == date(2026, 12, 10) and new.end == date(2026, 12, 13) and new.days == 4 and notes == ["Salida: 04/12/2026 → 10/12/2026"]
    new, notes, err, _ = P.apply_changes(cur, {"end": date(2026, 12, 9)}, TODAY)
    assert new.days == 6 and new.start == cur.start
    assert P.apply_changes(cur, {"days": 30}, TODAY)[2] and P.apply_changes(cur, {"travelers": 0}, TODAY)[2]
    assert P.apply_changes(cur, {"start": date(2026, 11, 1)}, TODAY)[2] and P.apply_changes(cur, {"budget": 10}, TODAY)[2]
    assert P.apply_changes(cur, {"destination": "Barranquilla"}, TODAY)[2]                 # destino igual al origen
    assert P.apply_changes(cur, {"end": date(2026, 12, 1)}, TODAY)[2]                      # regreso antes de la salida
    # un viaje con fecha ya pasada se puede seguir editando (solo se valida la fecha si se cambia)
    old = P.request_from_trip({**trip, "startDate": "01/01/2026", "endDate": "04/01/2026"})
    assert P.apply_changes(old, {"budget": 5000000}, TODAY)[2] is None
    assert P.request_from_trip({"tripId": "x"}) is None
    d = P.parse_dates("del 10 al 13 de diciembre", TODAY); assert d == [date(2026, 12, 10), date(2026, 12, 13)]
    assert P.parse_dates("el 3 de enero", TODAY) == [date(2027, 1, 3)] and P.parse_dates("el 10/12/2026", TODAY) == [date(2026, 12, 10)]
    print("OK: lectura y aplicacion de cambios (dias, presupuesto, viajeros, fechas, ciudades) y casos que no son cambios")


def test_budget_math():
    b = P.compute_budget(5_000_000, 3, 4, 77, True, 4000)
    assert b["breakdown"][0]["costCOP"] == 77 * 4000 * 2 * 3 and b["totalEstimatedCOP"] == 1_848_000 + 1_350_000 + 1_440_000
    assert b["withinBudget"] and b["remainingCOP"] == 5_000_000 - b["totalEstimatedCOP"]
    print("OK: presupuesto")


def test_lenguaje_claro():
    """'Chubascos' no es de uso general: el clima se muestra como lluvia. Y el precio no sale como 82.0."""
    assert P.friendly_condition("Chubascos ligeros") == "Lluvia ligera por ratos"
    assert P.friendly_condition("Chubascos fuertes") == "Lluvia fuerte por ratos"
    assert P.friendly_condition("Chubascos") == "Lluvia por ratos"
    assert P.friendly_condition("Despejado") == "Despejado" and P.friendly_condition(None) == ""
    w = {"days": [{"condition": "Chubascos ligeros", "temp_min_c": 17, "temp_max_c": 26, "rain_probability_pct": 90}]}
    assert "chubasc" not in P.weather_line(P.friendly_weather(w)).lower()
    assert P.usd(82.0) == "82" and P.usd(82.5) == "82.5" and P.usd(None) == "None"
    line = P.flight_line({"airline": "Wingo", "flight": "P57623", "depart": "12:32", "stops": 0, "price_usd": 82.0})
    assert "US$82" in line and "82.0" not in line, line
    # el texto de origen del Weather Agent tambien quedo en lenguaje claro
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("weather_core", pathlib.Path(__file__).resolve().parents[1] / "app" / "WeatherAgent" / "weather_core.py")
    wc = importlib.util.module_from_spec(spec); spec.loader.exec_module(wc)
    assert all("chubasc" not in v.lower() for v in wc.WMO.values()), wc.WMO
    print("OK: lenguaje claro (lluvia en vez de chubascos, precios sin .0)")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("TODAS LAS PRUEBAS DE planner.py OK")
