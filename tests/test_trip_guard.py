"""Guarda de datos faltantes del Orchestrator (deterministica, sin LLM ni AWS).
    python tests/test_trip_guard.py
"""
import os, sys, importlib.util, pathlib
os.environ.update(AWS_DEFAULT_REGION="us-east-1", AWS_ACCESS_KEY_ID="x", AWS_SECRET_ACCESS_KEY="x",
                  FLIGHTS_AGENT_URL="http://f", WEATHER_AGENT_URL="http://w")
ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("omain", str(ROOT / "app/OrchestratorAgent/main.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
need = m._missing_trip_info

DEMO = "Quiero organizar unas vacaciones de cuatro días en Cartagena. Salgo desde Bogotá, viajo con dos personas más y tengo un presupuesto de 5 millones de pesos. Quiero conocer el clima, encontrar vuelos y organizar algunas actividades turísticas."

# --- Peticiones COMPLETAS: nunca deben bloquearse ---
completos = [
    DEMO,
    "Quiero ir de Medellín a Cartagena 3 días, somos 2 y tenemos 4 millones",
    "Planea un viaje a Cali desde Bogotá para una persona con 2 millones",
    "Viajo solo desde Bogotá a Santa Marta, presupuesto 3 millones",
    "Organiza unas vacaciones en San Andrés, salgo de Bogotá con mi esposa y mis hijos, tengo $6.000.000",
    "Quiero irme de vacaciones a Cartagena desde Medellín, voy sola, presupuesto de 2500000 pesos",
    "Quiero ir a Pereira de Bogotá a Pereira con 3 amigos y 1.500.000 cop",
]
for t in completos:
    assert need(t, [], {}) == [], (t, need(t, [], {}))

# --- Frases reales de la prueba del usuario y variantes en espanol colombiano: tampoco deben bloquearse ---
completos2 = [
    "quiero salir de barranquilla atlantico, vamos 2 personas y tenemos 1.000.000 de pesos para 2 dias",
    "Quiero irme 4 dias a Santa Marta, salgo de Medellin, somos 3 y tengo 3 palos",
    "Quiero irme de vacaciones a Cartagena, vivo en Cali, voy con mi novia y tengo un millon y medio",
    "Quiero viajar a San Andres saliendo de Bogota con mi familia, presupuesto 8.000.000",
    "Quiero planear un viaje a Leticia: parto de Bogota, viajamos 4, 6 millones",
    "Quiero ir a Cartagena, voy desde Barranquilla, solo yo, tengo 800 mil pesos",
    "Quiero organizar un paseo a Medellin; salida desde Cali; dos personas; $2.500.000",
]
for t in completos2:
    assert need(t, [], {}) == [], (t, need(t, [], {}))
# la respuesta de la captura, dentro de la conversacion en dos tiempos
hq = [{"role": "user", "content": "hola que tal, quiero irme 4 días a Santa Marta"},
      {"role": "assistant", "content": "Para armarlo me faltan: la ciudad de salida, cuantas personas viajan y tu presupuesto en pesos."}]
assert need("quiero salir de barranquilla atlantico, vamos 2 personas y tenemos 1.000.000 de pesos para 2 días", hq, {}) == []
assert need("hola que tal, quiero irme 4 días a Santa Marta", [], {}) == ["la ciudad de salida", "cuantas personas viajan", "tu presupuesto en pesos"]

# --- Peticiones INCOMPLETAS: deben preguntar exactamente lo que falta ---
assert need("quiero irme 4 dias a santa marta", [], {}) == ["la ciudad de salida", "cuantas personas viajan", "tu presupuesto en pesos"]
assert need("Quiero ir a Cartagena desde Bogotá", [], {}) == ["cuantas personas viajan", "tu presupuesto en pesos"]
assert need("Quiero planear unas vacaciones en Cali, somos 3", [], {}) == ["la ciudad de salida", "tu presupuesto en pesos"]
assert need("Quiero irme de vacaciones a Santa Marta", [], {}) == ["la ciudad de salida", "cuantas personas viajan", "tu presupuesto en pesos"]  # 'de vacaciones a' NO es un origen
assert need("Quiero viajar desde el 4 de diciembre a Cartagena", [], {}) == ["la ciudad de salida", "cuantas personas viajan", "tu presupuesto en pesos"]  # 'desde el' NO es un origen

# --- Mensajes que NO son una peticion de viaje nueva: nunca se bloquean ---
for t in ["hola", "¿Cuáles son mis viajes guardados?", "reserva el vuelo más barato", "Cancela mi reserva",
          "Dame el clima de Cartagena del 04/12/2026 al 08/12/2026. Usa solo ask_weather y no guardes nada.",
          "Busca vuelos reales de Barranquilla a Cartagena para el 04/12/2026. Solo consulta con ask_flights y no guardes nada.",
          "cambia el destino a Medellín", "actualiza el presupuesto a 3 millones", "gracias"]:
    assert need(t, [], {}) == [], t
# con un viaje activo es una edicion, no un viaje nuevo
assert need("quiero irme 4 dias a santa marta", [], {"activeTrip": {"tripId": "x"}}) == []

# --- Conversacion en dos tiempos (la respuesta del usuario completa lo que faltaba) ---
h1 = [{"role": "user", "content": "quiero irme 4 dias a santa marta"},
      {"role": "assistant", "content": "Para armarlo me faltan: la ciudad de salida, cuantas personas viajan y tu presupuesto en pesos."}]
assert need("desde Bogotá, somos 2 y tengo 3 millones", h1, {}) == []
assert need("desde Bogotá", h1, {}) == ["cuantas personas viajan", "tu presupuesto en pesos"]
assert need("somos 2 desde Bogotá", h1, {}) == ["tu presupuesto en pesos"]
# no insistir indefinidamente ni cuando el usuario delega la decision
h2 = h1 + [{"role": "user", "content": "desde Bogotá"},
           {"role": "assistant", "content": "Para armarlo me faltan: cuantas personas viajan, tu presupuesto en pesos."}]
assert need("no se", h2, {}) == []                                  # ya pregunto 2 veces: sigue adelante
assert need("tu decide", h1, {}) == [] and need("Tú decide por mí", h1, {}) == []
# una pregunta de datos faltantes NO contamina mensajes posteriores no relacionados
assert need("¿Cuál es el clima en Cali?", h1[:1] + [{"role": "assistant", "content": "Claro, aqui tienes."}], {}) == []
print("OK: guarda de datos faltantes (", len(completos), "completos,", "incompletos, no-viaje y dos tiempos )")
