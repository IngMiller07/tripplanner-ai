# app/OrchestratorAgent/main.py  (v3 - proyecto final)
#
# Orquestador HTTP de AgentCore. Es el unico agente que habla con el usuario.
#
#  - Delega en Flights (vuelos reales via MCP/Kiwi) y Weather (clima real) por A2A.
#  - Tiene las 9 Tools de negocio (viajes, itinerarios, reservas) que invocan las
#    Lambdas con boto3 lambda.invoke  -> Canal 2 (agentico), SIN API Gateway.
#  - "Planear un viaje" y "reservar un vuelo" son flujos plan-and-execute (planner.py): el codigo
#    conduce los pasos y emite eventos de LINEA DE TIEMPO; el LLM interpreta, reparte el itinerario y
#    redacta. Las demas peticiones (cambiar, eliminar, consultar...) las resuelve el agente libre con Tools.
#  - Responde en STREAMING (Server-Sent Events): texto token a token + eventos
#    estructurados (tool_start / tool_result con JSON) para que el frontend pinte
#    tarjetas, tablas y timelines.
#  - La memoria gestionada de AgentCore es OPCIONAL: si TRIP_MEMORY_ID falta o
#    falla, el historial lo manda el frontend en el payload (history).

import os
import unicodedata
import re
import json
import uuid
import math
from datetime import date, timedelta
from typing import Optional

import urllib.error
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from strands import Agent, tool
from strands.agent.conversation_manager import SlidingWindowConversationManager
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
FLIGHTS_URL = os.environ.get("FLIGHTS_AGENT_URL", "")
WEATHER_URL = os.environ.get("WEATHER_AGENT_URL", "")
MEMORY_ID = os.environ.get("TRIP_MEMORY_ID") or None
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # llm_provider.py vive junto a main.py
from llm_provider import build_model  # Bedrock por defecto; LLM_PROVIDER=openai_compat usa una API externa
import planner as _planner  # flujo plan-and-execute y linea de tiempo
COP_PER_USD = float(os.environ.get("COP_PER_USD", "4000"))  # tasa de referencia

_lambda_client = boto3.client("lambda", region_name=AWS_REGION)


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------
def _as_json_text(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _extract_json(text: str):
    """Intenta sacar un objeto JSON de la respuesta de un agente especialista."""
    if not isinstance(text, str):
        return text
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    for candidate in (t, t[t.find("{"): t.rfind("}") + 1]):
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    return {"raw": text}


def _strip_thinking(text: str) -> str:
    """Quita el razonamiento <thinking>...</thinking> (aunque quede sin cerrar) del texto de un especialista."""
    if not isinstance(text, str):
        return text
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.S)
    i = text.find("<thinking>")
    if i >= 0:
        j = text.find("{", i)
        text = text[:i] + (text[j:] if j >= 0 else "")
    return text.strip()


def _call_a2a_agent(runtime_url: str, message_text: str):
    """Llama a un agente A2A en AgentCore Runtime (AWS_IAM) firmando con SigV4."""
    if not runtime_url:
        return {"error": "Falta configurar la URL del agente especialista (envVars)."}

    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": message_text}],
                "messageId": uuid.uuid4().hex,
            }
        },
    }
    body = json.dumps(payload).encode("utf-8")
    credentials = boto3.Session().get_credentials().get_frozen_credentials()
    aws_request = AWSRequest(
        method="POST",
        url=runtime_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": str(uuid.uuid4()),
        },
    )
    SigV4Auth(credentials, "bedrock-agentcore", AWS_REGION).add_auth(aws_request)
    # urllib (libreria estandar): no depende de 'requests' en el pyproject del agente.
    req = urllib.request.Request(runtime_url, data=body, headers=dict(aws_request.headers), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=110) as resp:
            raw_text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return {"error": f"Error {exc.code} del agente especialista: {detail}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"error": f"No se pudo contactar al agente: {exc}"}

    try:
        data = json.loads(raw_text)
    except ValueError:
        return {"raw": raw_text[:1500]}
    result = data.get("result", {})
    parts = result.get("parts") or result.get("status", {}).get("message", {}).get("parts")
    if not parts:
        for art in result.get("artifacts") or []:
            parts = (parts or []) + (art.get("parts") or [])
    if parts:
        text = " ".join(p.get("text", "") for p in parts if "text" in p)
        return _extract_json(_strip_thinking(text))
    return {"raw": json.dumps(data)[:1500]}


def _invoke_lambda(function_name: str, payload: dict) -> dict:
    """Invoca una Lambda de negocio de forma directa (sin API Gateway)."""
    response = _lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    raw = response["Payload"].read()
    result = json.loads(raw) if raw else {}
    if "FunctionError" in response:
        return {"error": f"Error en {function_name}: {result}"}
    return result


# --------------------------------------------------------------------------
# Tools: especialistas A2A
# --------------------------------------------------------------------------
def _clean_place(place: str) -> str:
    """'Barranquilla, Atlantico' -> 'Barranquilla' (el departamento o pais no es otra ciudad)."""
    return str(place or "").split(",")[0].strip()


def _to_dmy(value: str) -> str:
    """Acepta dd/mm/yyyy, dd-mm-yyyy o yyyy-mm-dd y devuelve dd/mm/yyyy (si no entiende el formato, lo deja igual)."""
    v = str(value or "").strip()
    m1 = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", v)
    if m1:
        return f"{int(m1.group(3)):02d}/{int(m1.group(2)):02d}/{m1.group(1)}"
    m2 = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$", v)
    if m2:
        return f"{int(m2.group(1)):02d}/{int(m2.group(2)):02d}/{m2.group(3)}"
    return v


def _specialist_obj(url: str, message: str, key: str, label: str, attempts: int = 2) -> dict:
    """Llama al especialista y exige un JSON con `key` (o con `error`). Si el modelo del especialista
    responde con texto o razonamiento en vez de JSON, reintenta con una peticion mas estricta."""
    result = {}
    for attempt in range(attempts):
        text = message if attempt == 0 else (message + " IMPORTANTE: responde UNICAMENTE el objeto JSON pedido, sin texto ni etiquetas adicionales.")
        result = _call_a2a_agent(url, text)
        if isinstance(result, dict) and (key in result or "error" in result):
            return result
        print(f"[agent] {label}: respuesta sin JSON valido (intento {attempt + 1}/{attempts}): {str(result)[:160]}")
    return {"error": f"El agente de {label} no devolvio un resultado valido. Intenta de nuevo."}


def _ask_specialist(url: str, message: str, key: str, label: str) -> str:
    return _as_json_text(_specialist_obj(url, message, key, label))


def _flights_request(origin: str, destination: str, date_dmy: str) -> str:
    o, d, dt = _clean_place(origin), _clean_place(destination), _to_dmy(date_dmy)
    return (f"Busca vuelos de ida. Origen: {o}. Destino: {d}. Fecha de salida: {dt}. "
            f"Llama UNA vez a search_flights con origin='{o}', destination='{d}', date='{dt}' y responde solo el JSON.")


def _weather_request(city: str, start_dmy: str, end_dmy: str = "") -> str:
    c, d0 = _clean_place(city), _to_dmy(start_dmy)
    d1 = _to_dmy(end_dmy) or d0
    return (f"Dame el clima. Ciudad: {c}. Desde {d0} hasta {d1}. "
            f"Llama UNA vez a get_forecast con city='{c}', start_date='{d0}', end_date='{d1}' y responde solo el JSON.")


@tool
def ask_flights(origin: str, destination: str, date: str) -> str:
    """Delega en el Flights Agent la busqueda de vuelos REALES de ida. Devuelve JSON con la lista de vuelos.

    Args:
        origin: ciudad de origen, SOLO la ciudad, sin departamento ni pais (ej. "Barranquilla").
        destination: ciudad de destino, solo la ciudad (ej. "Santa Marta").
        date: fecha de salida dd/mm/yyyy.
    """
    return _ask_specialist(FLIGHTS_URL, _flights_request(origin, destination, date), "flights", "vuelos")


@tool
def ask_weather(city: str, start_date: str, end_date: str = "") -> str:
    """Delega en el Weather Agent el clima REAL de una ciudad. Devuelve JSON con el clima dia por dia.

    Args:
        city: ciudad, SOLO la ciudad, sin departamento ni pais (ej. "Cartagena").
        start_date: fecha de inicio dd/mm/yyyy.
        end_date: fecha de fin dd/mm/yyyy (si se omite, un solo dia).
    """
    return _ask_specialist(WEATHER_URL, _weather_request(city, start_date, end_date), "days", "clima")


# --------------------------------------------------------------------------
# Tool: calculo de presupuesto (determinista, para no depender de aritmetica del LLM)
# --------------------------------------------------------------------------
@tool
def calculate_budget(budget_cop: float, travelers: int, days: int,
                     flight_price_usd_per_person: float = 0.0, round_trip: bool = True) -> str:
    """
    Calcula si el viaje cabe en el presupuesto (en pesos colombianos, COP).
    Estimados de referencia: hospedaje 150.000 COP/persona/noche y
    alimentacion + actividades 120.000 COP/persona/dia.

    Args:
        budget_cop: presupuesto total del grupo en COP.
        travelers: numero de viajeros.
        days: dias del viaje (las noches son days-1).
        flight_price_usd_per_person: precio de UN tiquete (ida) por persona en USD.
        round_trip: si True, cuenta ida y vuelta (x2).
    """
    return _as_json_text(_planner.compute_budget(budget_cop, travelers, days, flight_price_usd_per_person, round_trip, COP_PER_USD))


# --------------------------------------------------------------------------
# Tools de negocio (9) -> Lambdas (Canal 2). Mismas Lambdas que usa el
# frontend por API Gateway (Canal 1).
# --------------------------------------------------------------------------
@tool
def create_trip(userId: str, origin: str, destination: str, startDate: str, endDate: str,
                travelers: int = 1, budget: Optional[float] = None) -> str:
    """Crea un viaje y lo guarda en DynamoDB. Devuelve el viaje con su tripId.

    Args:
        userId: id del usuario actual (te lo dan en el contexto).
        origin: ciudad de origen.
        destination: ciudad de destino.
        startDate: fecha de inicio dd/mm/yyyy.
        endDate: fecha de fin dd/mm/yyyy.
        travelers: numero de viajeros.
        budget: presupuesto total en COP.
    """
    return _as_json_text(_invoke_lambda("TripPlanner-CreateTrip", {
        "userId": userId, "origin": origin, "destination": destination,
        "startDate": startDate, "endDate": endDate, "travelers": travelers, "budget": budget}))


@tool
def get_trips(userId: str) -> str:
    """Devuelve todos los viajes guardados del usuario."""
    return _as_json_text(_invoke_lambda("TripPlanner-GetTrips", {"userId": userId}))


@tool
def update_trip(tripId: str, origin: Optional[str] = None, destination: Optional[str] = None,
                startDate: Optional[str] = None, endDate: Optional[str] = None,
                travelers: Optional[int] = None, budget: Optional[float] = None,
                status: Optional[str] = None) -> str:
    """Actualiza campos de un viaje existente (solo envia los que cambian).
    status: PLANEANDO | RESERVADO | COMPLETADO | CANCELADO."""
    payload = {"tripId": tripId, "origin": origin, "destination": destination,
               "startDate": startDate, "endDate": endDate, "travelers": travelers,
               "budget": budget, "status": status}
    return _as_json_text(_invoke_lambda("TripPlanner-UpdateTrip",
                                        {k: v for k, v in payload.items() if v is not None}))


@tool
def create_itinerary(tripId: str, days: list) -> str:
    """Guarda el itinerario de un viaje ya creado.

    Args:
        tripId: id del viaje (lo devuelve create_trip).
        days: lista de dias. Ejemplo:
            [{"day": 1, "title": "Llegada", "activities": ["Vuelo BOG-CTG", "Check-in hotel"]},
             {"day": 2, "title": "Ciudad amurallada", "activities": ["Tour a pie", "Cena en Getsemani"]}]
    """
    return _as_json_text(_invoke_lambda("TripPlanner-CreateItinerary", {"tripId": tripId, "days": days}))


@tool
def update_itinerary(tripId: str, itineraryId: str, days: list) -> str:
    """Reemplaza los dias de un itinerario existente (envia la lista completa de dias)."""
    return _as_json_text(_invoke_lambda("TripPlanner-UpdateItinerary",
                                        {"tripId": tripId, "itineraryId": itineraryId, "days": days}))


@tool
def create_reservation(tripId: str, type: str, details: dict) -> str:
    """Registra una reserva SIMULADA (no hay proveedor real) pero persistida en DynamoDB.

    Args:
        tripId: id del viaje.
        type: VUELO | HOTEL | ACTIVIDAD.
        details: datos de la reserva (ej. aerolinea, vuelo, horario, precio).
    """
    return _as_json_text(_invoke_lambda("TripPlanner-CreateReservation",
                                        {"tripId": tripId, "type": type, "details": details}))


@tool
def get_reservations(tripId: str) -> str:
    """Devuelve todas las reservas de un viaje."""
    return _as_json_text(_invoke_lambda("TripPlanner-GetReservations", {"tripId": tripId}))


@tool
def update_reservation(tripId: str, reservationId: str, status: str) -> str:
    """Cambia el estado de una reserva. status: SIMULADA | PENDIENTE | CONFIRMADA."""
    return _as_json_text(_invoke_lambda("TripPlanner-UpdateReservation",
                                        {"tripId": tripId, "reservationId": reservationId, "status": status}))


@tool
def delete_reservation(tripId: str, reservationId: str) -> str:
    """Elimina (borra) una reserva de DynamoDB. Si no tienes el reservationId, llama primero a get_reservations.

    Args:
        tripId: id del viaje al que pertenece la reserva.
        reservationId: id de la reserva que se va a eliminar.
    """
    return _as_json_text(_invoke_lambda("TripPlanner-DeleteReservation",
                                        {"tripId": tripId, "reservationId": reservationId}))


TOOLS = [ask_flights, ask_weather, calculate_budget, create_trip, get_trips, update_trip,
         create_itinerary, update_itinerary, create_reservation, get_reservations,
         update_reservation, delete_reservation]


# --------------------------------------------------------------------------
# Prompt y construccion del agente
# --------------------------------------------------------------------------
# Lugares reales por destino (catalogo en planner.py; el itinerario se arma SOLO con estos).
_PLACES = {c: ", ".join(v) for c, v in _planner.PLACES.items()}
_PLACES_TXT = "\n".join(f"- {c}: {v}." for c, v in _PLACES.items())


def _system_prompt(user_id: str, ctx: dict) -> str:
    today = date.today()
    default_start = today + timedelta(days=10)
    ctx_txt = json.dumps(ctx, ensure_ascii=False)[:2500] if ctx else "{}"
    return f"""Eres TripPlanner AI, un asistente que planifica viajes de punta a punta. Responde siempre en espanol, claro y amable.

CONTEXTO
- Hoy es {today.strftime('%d/%m/%Y')}. El userId del usuario es "{user_id}" (usalo en create_trip y get_trips).
- Contexto de la conversacion (viaje activo, vuelos ya mostrados, etc.): {ctx_txt}

HERRAMIENTAS
- ask_weather(ciudad, fecha inicio y fin): clima real del destino.
- ask_flights(origen, destino, fecha dd/mm/yyyy): vuelos reales (precios en USD).
- calculate_budget: SIEMPRE usala para el presupuesto (no calcules a mano). Presupuestos y totales van en COP.
- create_trip / get_trips / update_trip, create_itinerary / update_itinerary,
  create_reservation / get_reservations / update_reservation / delete_reservation: guardan, leen y borran en la base de datos.

SALUDOS Y CONVERSACION
- Si el mensaje actual es solo un saludo o charla (hola, buenas, gracias, que puedes hacer), responde con un saludo corto y amable, di en una frase que puedes buscar vuelos, revisar el clima, armar el itinerario y guardar el viaje, y pregunta a donde quiere viajar. NO llames herramientas y NO retomes pedidos anteriores del historial a menos que el usuario lo pida ahora.
- Solo planifica un viaje cuando el mensaje actual lo pide.
- Los recuerdos que aparezcan de conversaciones anteriores (preferencias, datos del usuario) son SOLO contexto de apoyo. Nunca los trates como un pedido nuevo ni empieces a planificar por ellos: actua unicamente sobre lo que el usuario escribe ahora.

CUANDO EL USUARIO PIDA ORGANIZAR UN VIAJE
1. Extrae destino, origen, numero de dias, viajeros y presupuesto (usa tambien lo que el usuario ya dijo antes en esta conversacion o que aparezca en el contexto del viaje activo). NUNCA inventes el origen, el numero de viajeros ni el presupuesto: si falta alguno, NO llames herramientas todavia; haz UNA sola pregunta corta que pida exactamente lo que falta (por ejemplo: "Para armarlo me faltan: la ciudad de salida, cuantas personas viajan y tu presupuesto en pesos.") y espera la respuesta. Solo si el usuario te pide que decidas tu, asume valores razonables y dilo explicitamente. Las fechas SI puedes asumirlas: si NO da fechas, asume salida el {default_start.strftime('%d/%m/%Y')} y regreso segun los dias pedidos, y dilo explicitamente ("asumi estas fechas, dime si quieres cambiarlas").
2. Llama ask_weather para el destino en esas fechas y ask_flights para el vuelo de ida origen->destino. Haz ambas consultas antes de responder.
3. Con el vuelo mas barato llama calculate_budget (viajeros = TODAS las personas del grupo).
4. Disena un itinerario dia por dia (3 a 4 actividades concretas por dia) adaptado al clima (si hay lluvia, actividades bajo techo ese dia). Usa solo lugares y actividades reales del destino pedido; no copies sitios de otra ciudad ni inventes precios.
5. Guarda el viaje con create_trip (travelers = TODAS las personas del grupo, incluido el usuario; budget en COP) y luego el plan con create_itinerary (usa el tripId que devolvio create_trip). Hazlo sin pedir permiso: guardar no cuesta nada.
6. Cierra con un resumen BREVE (4 a 6 lineas). La pantalla ya muestra tarjetas con el clima, los vuelos, el presupuesto y el itinerario, asi que NO los repitas dato por dato: destaca lo importante (como estara el clima, el vuelo mas barato, cuanto sobra o falta del presupuesto, las fechas que asumiste). No muestres IDs. Termina ofreciendo reservar el vuelo (reserva SIMULADA).
7. NO crees reservas hasta que el usuario lo pida. Cuando lo pida, usa create_reservation (type VUELO, HOTEL o ACTIVIDAD) con los detalles reales del vuelo elegido y avisa que es simulada.
- Si el usuario pide cambiar algo, usa update_trip / update_itinerary / update_reservation. Si pregunta por sus viajes, usa get_trips.
- ELIMINAR una reserva: usa delete_reservation(tripId, reservationId). Si no tienes el reservationId en el contexto, llama primero a get_reservations con el tripId del viaje activo, elige la reserva que el usuario menciona (por tipo o descripcion) y borrala. Si hay varias posibles y no es claro cual, pregunta. Confirma al terminar que ya no existe.
- Nunca inventes vuelos, precios ni clima: usa solo lo que devuelven las herramientas.
- No pidas ni aceptes datos de tarjetas de credito; las reservas son simuladas.

LUGARES PARA EL ITINERARIO (cada actividad debe ser de la ciudad destino)
{_PLACES_TXT}
- Si el destino esta en esta lista, arma el itinerario SOLO con sus lugares (mezcla 3 a 4 por dia, sin repetir). Nunca uses lugares de otra ciudad (por ejemplo, la Ciudad Amurallada es solo de Cartagena).
- No nombres restaurantes, hoteles ni tiendas concretos: escribe actividades como "almuerzo de comida tipica de la region". Si el destino no esta en la lista, usa solo actividades genericas que existan seguro (recorrido por el centro historico, tarde de playa, museo local) sin inventar nombres.

REGLAS DE EJECUCION (muy importantes)
- Trabaja en silencio hasta terminar: NO escribas mensajes intermedios como "voy a calcular...", "mientras tanto...", "espera un momento" o "intentare de nuevo". Llama a las herramientas directamente.
- NO termines tu turno hasta haber completado los pasos 2 a 5 de un viaje. El unico texto para el usuario es el resumen final del paso 6.
- Si ask_weather devuelve un error, reintentalo UNA vez; si vuelve a fallar, sigue con el resto (vuelos, presupuesto, itinerario sin depender del clima, guardado) y menciona al final que el clima no estuvo disponible.
- Si ask_flights devuelve un error, reintentalo UNA vez; si vuelve a fallar, dilo en el resumen.
- Nunca escribas etiquetas <thinking> ni muestres tu razonamiento interno.
- Habla como una persona a otra. NUNCA nombres piezas tecnicas al usuario (agentes, herramientas, nombres de funciones como create_trip, Lambda, DynamoDB, MCP, A2A, Nova, Orchestrator): di "guarde tu viaje", "consulte el clima", "busque vuelos".
- Si el usuario nombra solo un PAIS como destino (Brasil, Mexico, Espana...), pidele una ciudad concreta (por ejemplo "Rio de Janeiro o Sao Paulo") antes de usar herramientas."""


_GREET_CORE = {"hola", "holi", "ola", "buenas", "buenos", "buen", "hey", "hi", "hello", "saludos", "gracias"}
_GREET_ALL = _GREET_CORE | {"dia", "dias", "tarde", "tardes", "noche", "noches", "que", "tal", "como", "estas", "esta",
                            "muchas", "mil", "jarvis", "asistente", "tripplanner", "ai"}

_GREETING_TURN = """

ESTE TURNO (instruccion del sistema, prioridad maxima)
El usuario SOLO esta saludando o agradeciendo. Responde en maximo 2 frases: un saludo amable, di que puedes buscar vuelos, consultar el clima, armar el itinerario y guardar el viaje, y pregunta a donde quiere viajar. No tienes herramientas en este turno. No planifiques nada ni menciones pedidos anteriores."""


def _is_greeting(prompt: str) -> bool:
    """True si el mensaje es solo un saludo/agradecimiento ('hola', 'buenas tardes', 'gracias')."""
    words = re.findall(r"[a-z]+", _norm_text(prompt))
    return 0 < len(words) <= 5 and all(w in _GREET_ALL for w in words) and any(w in _GREET_CORE for w in words)


_NUM = r"(?:\d+|un|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)"
_NOT_CITY = (r"(?!el\b|la\b|los\b|las\b|hoy\b|manana\b|ayer\b|\d|vacaciones|viaje|paseo|escapada|fin\b|puente|casa\b|trabajo\b"
             r"|enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)")
_TRIP_INTENT = re.compile(r"\b(vacaciones|viajar|viaje|irme|irnos|ir a|escapada|paseo|planear|planea|planifica\w*|organiza\w*|itinerario|turismo)\b")
_NOT_PLANNING = re.compile(r"\b(mis viajes|mis reservas|guardad\w+|cancel\w+|reserva\w*|actualiza\w*|cambia\w*|modifica\w*|elimina\w*|borra\w*)\b")
_YOU_DECIDE = re.compile(r"\b(tu decide|tu decides|decide tu|asume|asumelo|supon|cualquiera|da igual|no importa|lo que sea)\b")
_HAS_ORIGIN = re.compile(
    rf"\b(?:desde|origen)\s+{_NOT_CITY}\w"
    rf"|\b(?:salir|salgo|salimos|salida|saliendo|partir|parto|partimos|partiendo|viajo|viajamos|voy|vamos|vengo|venimos)\s+(?:de|desde)\s+{_NOT_CITY}\w"
    rf"|\b(?:vivo|vivimos|estoy|estamos|resido)\s+en\s+{_NOT_CITY}\w"
    rf"|\bde\s+{_NOT_CITY}[a-z]+(?:\s[a-z]+)?\s+(?:a|hacia|para)\s+[a-z]")
_HAS_TRAVELERS = re.compile(
    rf"\b{_NUM}\s+(?:personas?|viajeros?|adultos?|pasajeros?|amigos?|ninos?|hijos?)\b"
    rf"|\b(?:somos|seremos|vamos|viajamos)\s+{_NUM}\b"
    r"|\b(?:somos|seremos|viajamos)\b"
    r"|\b(?:nosotros|los)\s+(?:dos|tres|cuatro|cinco|seis)\b"
    r"|\b(?:voy|viajo|iria|ire|salgo|viajare)\s+(?:solo|sola|sin compania)\b"
    r"|\b(?:solo yo|solamente yo|para mi|pareja|familia|grupo)\b"
    r"|\b(?:con|junto a)\s+(?:mi|mis|" + _NUM + r")\s+(?:esposa|esposo|novia|novio|pareja|familia|amigos?|hijos?|hermanos?|papas|mama|papa|companer\w+)\b"
    r"|\bmi\s+(?:esposa|esposo|novia|novio|pareja|familia|hijos?|amigos?)\b")
_HAS_BUDGET = re.compile(
    r"\bpresupuesto\b|\$\s*\d"
    rf"|\b(?:{_NUM}|medio)\s+millon(?:es)?\b"
    r"|\b\d[\d.,]*\s*(?:de\s+)?(?:millon(?:es)?|mil|k|cop|pesos|usd|dolares|lucas?|palos?)\b"
    r"|\b\d{1,3}(?:[.,]\d{3})+\b|\b\d{5,}\b"
    r"|\b(?:tengo|tenemos|cuento con|contamos con|dispongo de|disponemos de|gastar|gastare|maximo)\s+(?:unos?\s+)?\d")

_MISSING_TURN = """

ESTE TURNO (instruccion del sistema, prioridad maxima)
El usuario quiere planear un viaje pero todavia NO ha dicho: {lista}. No tienes herramientas en este turno. Responde con UNA sola pregunta corta que empiece exactamente con "Para armarlo me faltan:" y pida solo eso. No asumas ni inventes esos datos, no planifiques y no menciones precios."""


def _missing_trip_info(prompt: str, history, ctx) -> list:
    """Datos esenciales que el usuario aun NO ha dado para planear un viaje (origen, viajeros, presupuesto).
    Lista vacia = no hay nada que preguntar (o el mensaje no es una peticion de viaje nueva)."""
    if isinstance(ctx, dict) and ctx.get("activeTrip"):
        return []                                               # ya hay un viaje en curso: es una edicion, no un viaje nuevo
    now = _norm_text(prompt)
    hist = [h for h in (history or []) if isinstance(h, dict)]
    assistant = [_norm_text(str(h.get("content") or h.get("text") or "")) for h in hist if h.get("role") == "assistant"]
    asked = bool(assistant) and "me faltan" in assistant[-1]    # la ultima respuesta fue la pregunta de datos faltantes
    if sum("me faltan" in a for a in assistant[-3:]) >= 2 or _YOU_DECIDE.search(now):
        return []                                               # no insistir: si el usuario prefiere que decida el agente, adelante
    if not asked and (_NOT_PLANNING.search(now) or not _TRIP_INTENT.search(now)):
        return []
    users = [_norm_text(str(h.get("content") or h.get("text") or "")) for h in hist if h.get("role") == "user"]
    text = " . ".join(users + [now]) if asked else now
    missing = []
    if not _HAS_ORIGIN.search(text):
        missing.append("la ciudad de salida")
    if not _HAS_TRAVELERS.search(text):
        missing.append("cuantas personas viajan")
    if not _HAS_BUDGET.search(text):
        missing.append("tu presupuesto en pesos")
    return missing


def _join_es(items) -> str:
    items = [str(i) for i in items]
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1] if items else ""


def _assistant_texts(history) -> list:
    return [_norm_text(str(h.get("content") or h.get("text") or ""))
            for h in (history or []) if isinstance(h, dict) and h.get("role") == "assistant"]


def _is_plan_request(prompt: str, history, ctx) -> bool:
    """True si el mensaje pide PLANEAR un viaje (nuevo) o contesta la pregunta de datos faltantes."""
    now = _norm_text(prompt)
    if _NOT_PLANNING.search(now):
        return False
    assistant = _assistant_texts(history)
    if assistant and "me faltan" in assistant[-1]:
        return True                                              # el usuario esta completando los datos que se le pidieron
    if not _TRIP_INTENT.search(now):
        return False
    active = ctx.get("activeTrip") if isinstance(ctx, dict) else None
    if not active:
        return True
    # ya hay un viaje en la conversacion: es uno NUEVO solo si nombra otro destino o trae los tres datos completos
    cities = [c for _, _, c in _planner.scan_cities(prompt)]
    if any(_norm_text(c) != _norm_text(active.get("destination")) for c in cities):
        return True
    return bool(_HAS_ORIGIN.search(now) and _HAS_TRAVELERS.search(now) and _HAS_BUDGET.search(now))


_RESERVE_FLIGHT = re.compile(r"\breserv\w*\b.*\b(vuelo|pasaje|tiquete|tiquetes|boleto|ticket)\b|\b(vuelo|pasaje|tiquete|boleto)\b.*\breserv\w*\b")
_NOT_RESERVE = re.compile(r"\b(elimin\w*|borr\w*|cancel\w*|cambi\w*|actualiz\w*|confirm\w*|hotel|actividad\w*|tour)\b")


# Eliminar reservas: lo decide el codigo (el modelo, ante la duda, borraba TODAS).
_DELETE_RESERVATION = re.compile(r"\b(elimin\w*|borr\w*|cancel\w*|quit\w*)\b.*\breserv\w*\b|\breserv\w*\b.*\b(elimin\w*|borr\w*|cancel\w*)\b")


def _is_delete_reservation_request(prompt: str) -> bool:
    return bool(_DELETE_RESERVATION.search(_norm_text(prompt)))


def _is_delete_followup(prompt: str, history) -> bool:
    """El asistente pregunto 'cual elimino?' y el usuario contesta corto ('la 2', 'la ultima', 'todas')."""
    assistant = _assistant_texts(history)
    return bool(assistant and _planner.ASK_MARKER in assistant[-1] and len(prompt.split()) <= 8)


def _clean_flight(f) -> Optional[dict]:
    """El vuelo viaja desde el navegador: solo se aceptan los campos esperados y con tipos simples."""
    if not isinstance(f, dict) or not (f.get("flight") or f.get("airline")):
        return None
    out = {}
    for k in ("airline", "flight", "depart", "arrive"):
        if isinstance(f.get(k), str):
            out[k] = f[k][:40]
    for k in ("price_usd", "stops"):
        if isinstance(f.get(k), (int, float)) and not isinstance(f.get(k), bool):
            out[k] = f[k]
    return out


def _reserve_flight_request(prompt: str, ctx):
    """Reserva de vuelo pedida en texto ('reserva el vuelo mas barato') con vuelos y viaje ya en pantalla. -> (vuelo, viaje) | None"""
    if not isinstance(ctx, dict):
        return None
    trip, lf = ctx.get("activeTrip"), ctx.get("lastFlights")
    if not (isinstance(trip, dict) and trip.get("tripId") and isinstance(lf, dict) and lf.get("options")):
        return None
    now = _norm_text(prompt)
    if not _RESERVE_FLIGHT.search(now) or _NOT_RESERVE.search(now):
        return None
    flight = _clean_flight(_planner.pick_flight(prompt, [o for o in lf["options"] if _clean_flight(o)]))
    return (flight, trip) if flight else None


def _build_session_manager(session_id: str, actor_id: str):
    """Memoria gestionada de AgentCore. None si no hay MEMORY_ID o si falla."""
    if not MEMORY_ID:
        return None
    try:
        from bedrock_agentcore.memory.integrations.strands.config import (
            AgentCoreMemoryConfig, RetrievalConfig,
        )
        from bedrock_agentcore.memory.integrations.strands.session_manager import (
            AgentCoreMemorySessionManager,
        )
        config = AgentCoreMemoryConfig(
            memory_id=MEMORY_ID, session_id=session_id, actor_id=actor_id,
            retrieval_config={
                "/preferences/{actorId}/": RetrievalConfig(top_k=5, relevance_score=0.3),
                "/facts/{actorId}/": RetrievalConfig(top_k=3, relevance_score=0.5),
            },
        )
        return AgentCoreMemorySessionManager(config, region_name=AWS_REGION)
    except Exception as exc:  # memoria es opcional: nunca debe tumbar el chat
        print(f"[memory] deshabilitada: {exc}")
        return None


def _history_to_messages(history) -> list:
    msgs = []
    for h in (history or [])[-20:]:
        role = h.get("role")
        text = h.get("content") if isinstance(h.get("content"), str) else h.get("text")
        if role in ("user", "assistant") and isinstance(text, str) and text.strip():
            msgs.append({"role": role, "content": [{"text": text[:4000]}]})
    # Bedrock exige que la conversacion empiece con 'user' y alterne roles.
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    clean = []
    for m in msgs:
        if clean and clean[-1]["role"] == m["role"]:
            clean[-1] = m
        else:
            clean.append(m)
    if clean and clean[-1]["role"] == "user":
        clean.pop()
    return clean


def _summary_system_prompt(facts: str) -> str:
    return f"""Eres TripPlanner AI, un asistente que planifica viajes. Responde siempre en espanol, claro y amable.

Acabas de armar el plan de un viaje (o de actualizar uno ya guardado) con datos reales, y quedó guardado. La pantalla ya muestra el clima, los vuelos, el presupuesto y el itinerario en tarjetas.

RESULTADOS REALES (usa solo estos datos y estas cifras exactas):
{facts}

Escribe SOLO el resumen final para el usuario: 4 a 6 lineas, sin repetir todo dato por dato. Destaca como estara el clima, el vuelo mas barato, si el presupuesto alcanza (cuanto sobra o falta) y los supuestos que se hicieron. Si algo fallo (clima, vuelos o guardado), dilo con claridad y di que puede pedir reintentarlo. No muestres IDs. No llames herramientas ni digas que vas a hacer algo: ya esta hecho. Si el primer dato dice ACTUALIZACIÓN, empieza diciendo qué cambió y cómo queda el presupuesto. Termina ofreciendo reservar el vuelo mas barato (la reserva es SIMULADA). Habla como una persona a otra: NUNCA nombres piezas tecnicas (agentes, herramientas, Lambda, DynamoDB, MCP, A2A, Nova, Orchestrator, nombres de funciones). Nunca escribas etiquetas <thinking>."""


def build_agent(session_id: str, user_id: str, ctx: dict, history, greeting_only: bool = False, missing=None,
                summary_facts: Optional[str] = None) -> Agent:
    no_tools = greeting_only or bool(missing) or bool(summary_facts)
    extra = _GREETING_TURN if greeting_only else (_MISSING_TURN.format(lista=", ".join(missing)) if missing else "")
    kwargs = {
        "name": "Trip Planner Orchestrator",
        "system_prompt": _summary_system_prompt(summary_facts) if summary_facts else _system_prompt(user_id, ctx) + extra,
        "model": build_model(MODEL_ID),
        "tools": [] if no_tools else TOOLS,
        "callback_handler": None,
    }
    session_manager = _build_session_manager(session_id, user_id)
    if session_manager is not None:
        kwargs["session_manager"] = session_manager
    else:
        kwargs["conversation_manager"] = SlidingWindowConversationManager(window_size=30)
        kwargs["messages"] = _history_to_messages(history)
    try:
        return Agent(**kwargs)
    except Exception as exc:
        if session_manager is None:
            raise
        print(f"[memory] fallo al crear el agente con memoria, sigo sin ella: {exc}")
        kwargs.pop("session_manager")
        kwargs["conversation_manager"] = SlidingWindowConversationManager(window_size=30)
        kwargs["messages"] = _history_to_messages(history)
        return Agent(**kwargs)


def _parse_tool_content(content) -> object:
    """toolResult.content -> JSON si es posible, si no texto."""
    texts = []
    for c in content or []:
        if "json" in c:
            return c["json"]
        if "text" in c:
            texts.append(c["text"])
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except (ValueError, TypeError):
        return joined


class _ThinkingFilter:
    """Quita los bloques <thinking>...</thinking> del texto que se envia al usuario (funciona aunque
    las etiquetas lleguen partidas entre varios fragmentos del stream)."""
    OPEN, CLOSE = "<thinking>", "</thinking>"

    def __init__(self):
        self.buf, self.inside = "", False

    @staticmethod
    def _partial(text: str, tag: str) -> int:
        for n in range(min(len(tag) - 1, len(text)), 0, -1):
            if text.endswith(tag[:n]):
                return n
        return 0

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        out = []
        while True:
            if self.inside:
                i = self.buf.find(self.CLOSE)
                if i < 0:
                    keep = self._partial(self.buf, self.CLOSE)
                    self.buf = self.buf[len(self.buf) - keep:] if keep else ""
                    break
                self.buf, self.inside = self.buf[i + len(self.CLOSE):].lstrip("\n"), False
            else:
                i = self.buf.find(self.OPEN)
                if i < 0:
                    keep = self._partial(self.buf, self.OPEN)
                    out.append(self.buf[: len(self.buf) - keep])
                    self.buf = self.buf[len(self.buf) - keep:] if keep else ""
                    break
                out.append(self.buf[:i])
                self.buf, self.inside = self.buf[i + len(self.OPEN):], True
        return "".join(out)

    def flush(self) -> str:
        rest = "" if self.inside else self.buf
        self.buf, self.inside = "", False
        return rest


_UNFINISHED = re.compile(
    r"(calculare|voy a |procedere|a continuacion|mientras tanto|un momento|espera|intentare|reintentare|"
    r"consultare|disenare|guardare|elaborare|preparare|buscare)")


def _norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _looks_unfinished(agent) -> bool:
    """True si el agente cerro el turno anunciando algo que no hizo (p. ej. 'calculare el presupuesto...')."""
    try:
        last = agent.messages[-1]
    except Exception:
        return False
    if last.get("role") != "assistant":
        return False
    blocks = last.get("content") or []
    if any("toolUse" in b for b in blocks):
        return False
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.S).strip()
    if not text or text.endswith("?"):
        return False
    return bool(_UNFINISHED.search(_norm_text(text[-350:])))


_NUDGE = ("Continua ahora y completa los pasos pendientes llamando directamente a las herramientas necesarias. "
          "No anuncies lo que vas a hacer: hazlo. Cuando termines, entrega el resumen final.")

# --------------------------------------------------------------------------
# Dependencias reales del flujo plan-and-execute (en pruebas se reemplazan por simulaciones)
# --------------------------------------------------------------------------
def _llm_text(system: str, prompt: str) -> str:
    """Una llamada corta al modelo (extraccion / reparto del itinerario), sin herramientas ni memoria."""
    helper = Agent(name="Planner helper", system_prompt=system, model=build_model(MODEL_ID), tools=[], callback_handler=None)
    return str(helper(prompt))


def _plan_deps() -> "_planner.Deps":
    return _planner.Deps(
        flights=lambda o, d, dt: _specialist_obj(FLIGHTS_URL, _flights_request(o, d, dt), "flights", "vuelos"),
        weather=lambda c, a, b: _specialist_obj(WEATHER_URL, _weather_request(c, a, b), "days", "clima"),
        invoke_lambda=_invoke_lambda, llm_text=_llm_text, fx=COP_PER_USD)


# --------------------------------------------------------------------------
# Un turno del agente en streaming (texto + tools) -> eventos
# --------------------------------------------------------------------------
async def _stream_turn(agent, current: str, names: dict, started: set):
    flt = _ThinkingFilter()
    async for ev in agent.stream_async(current):
        if not isinstance(ev, dict):
            continue
        if "data" in ev and isinstance(ev["data"], str):
            visible = flt.feed(ev["data"])
            if visible:
                yield {"type": "text", "data": visible}
            continue
        tu = ev.get("current_tool_use")
        if isinstance(tu, dict) and tu.get("toolUseId") and tu.get("name"):
            names[tu["toolUseId"]] = tu["name"]
            if tu["toolUseId"] not in started:
                started.add(tu["toolUseId"])
                yield {"type": "tool_start", "name": tu["name"], "id": tu["toolUseId"]}
        msg = ev.get("message")
        if isinstance(msg, dict):
            for block in msg.get("content", []) or []:
                if "toolUse" in block:
                    t = block["toolUse"]
                    names[t["toolUseId"]] = t["name"]
                    if t["toolUseId"] not in started:
                        started.add(t["toolUseId"])
                        yield {"type": "tool_start", "name": t["name"], "id": t["toolUseId"]}
                elif "toolResult" in block:
                    r = block["toolResult"]
                    yield {"type": "tool_result", "id": r.get("toolUseId"),
                           "name": names.get(r.get("toolUseId")),
                           "status": r.get("status", "success"),
                           "data": _parse_tool_content(r.get("content"))}
    tail = flt.flush()
    if tail:
        yield {"type": "text", "data": tail}


async def _plan_turn(prompt, history, ctx, user_id, session_id, names, started):
    """Planear un viaje: el codigo conduce los pasos (linea de tiempo) y el LLM redacta el resumen."""
    hist = [h for h in (history or []) if isinstance(h, dict)]
    assistant = _assistant_texts(hist)
    asked = bool(assistant) and "me faltan" in assistant[-1]
    users = [str(h.get("content") or h.get("text") or "") for h in hist if h.get("role") == "user"]
    text = " . ".join(users[-3:] + [prompt]) if asked else prompt
    allow_defaults = bool(_YOU_DECIDE.search(_norm_text(prompt))) or sum("me faltan" in a for a in assistant[-3:]) >= 2
    state = {"userId": user_id}
    async for ev in _planner.run_plan(text, _plan_deps(), state, allow_defaults=allow_defaults):
        yield ev
    if not state.get("ok"):
        if state.get("missing"):
            yield {"type": "text", "data": "Para armarlo me faltan: " + _join_es(state["missing"]) + ". Cuéntamelo y lo armo enseguida."}
        else:
            yield {"type": "text", "data": "No pude armar el plan en este momento. Intenta de nuevo."}
        return
    async for ev in _finish_turn(state, prompt, history, ctx, user_id, session_id, names, started):
        yield ev


async def _finish_turn(state, prompt, history, ctx, user_id, session_id, names, started):
    """Cierre de un plan o de una actualizacion: el LLM redacta el resumen con los datos reales (o la plantilla) y salen los botones."""
    got_text = False
    try:
        agent = build_agent(session_id, user_id, ctx, history, summary_facts="\n".join("- " + line for line in state["facts"]))
        async for ev in _stream_turn(agent, prompt, names, started):
            if ev.get("type") == "text" and str(ev.get("data", "")).strip():
                got_text = True
            yield ev
    except Exception as exc:
        print(f"[plan] el resumen con LLM fallo, uso la plantilla: {type(exc).__name__}: {exc}")
    if not got_text:
        yield {"type": "text", "data": _planner.template_summary(state["facts"])}
    if state.get("suggestions"):
        yield {"type": "suggestions", "items": state["suggestions"]}


def _update_request(prompt: str, history, ctx) -> Optional[dict]:
    """Cambio sobre el viaje activo (dias, presupuesto, viajeros, fechas, ciudades). None si el mensaje no es un cambio."""
    active = ctx.get("activeTrip") if isinstance(ctx, dict) else None
    if not isinstance(active, dict) or not active.get("tripId"):
        return None
    cur = _planner.request_from_trip(active)
    if cur is None:
        return None
    assistant = _assistant_texts(history)
    followup = bool(assistant) and _planner.UPDATE_ASK_MARKER in assistant[-1]
    res = _planner.parse_changes(prompt, cur, date.today(), followup)
    return None if res is None else {**res, "cur": cur, "active": active}


async def _update_turn(upd: dict, prompt, history, ctx, user_id, session_id, names, started):
    cur, ch = upd["cur"], upd["changes"]
    if upd.get("ask"):
        yield {"type": "text", "data": upd["ask"]}
        return
    req, notes, err, same = _planner.apply_changes(cur, ch, date.today())
    if err:
        yield {"type": "text", "data": err}
        return
    if not notes:
        yield {"type": "text", "data": f"Tu viaje ya tiene {_join_es(same) or 'esos datos'}, así que no hay nada que cambiar. "
                                       "Dime qué quieres modificar (días, presupuesto, viajeros o fechas)."}
        return
    lf = ctx.get("lastFlights") if isinstance(ctx.get("lastFlights"), dict) else {}
    flights = lf.get("options") if isinstance(lf.get("options"), list) else None
    same_route = (_norm_text(str(lf.get("route") or "")) == _norm_text(f"{req.origin}-{req.destination}")
                  and str(lf.get("date") or "") == _planner.dmy(req.start))
    itin_id = ctx.get("activeItineraryId") if isinstance(ctx.get("activeItineraryId"), str) else None
    base = _planner.update_plan(cur, req, upd["active"], itin_id, flights if same_route else None, notes)
    state = {"userId": user_id}
    async for ev in _planner.run_update(base, _plan_deps(), state):
        yield ev
    if not state.get("ok"):
        yield {"type": "text", "data": "No pude actualizar el viaje en este momento. Intenta de nuevo."}
        return
    async for ev in _finish_turn(state, prompt, history, ctx, user_id, session_id, names, started):
        yield ev


async def _reserve_turn(flight: dict, trip: dict, user_id: str):
    state: dict = {}
    async for ev in _planner.run_reserve(flight, trip, _plan_deps(), state):
        yield ev
    yield {"type": "text", "data": state.get("text") or ""}
    if state.get("suggestions"):
        yield {"type": "suggestions", "items": state["suggestions"]}


async def _delete_turn(prompt: str, trip: Optional[dict], user_id: str, sel: Optional[dict] = None):
    state: dict = {}
    async for ev in _planner.run_delete(prompt, _plan_deps(), state, trip=trip, user_id=user_id, sel=sel):
        yield ev
    yield {"type": "text", "data": state.get("text") or ""}
    if state.get("suggestions"):
        yield {"type": "suggestions", "items": state["suggestions"]}


def _active_trip(ctx) -> Optional[dict]:
    t = ctx.get("activeTrip") if isinstance(ctx, dict) else None
    return t if isinstance(t, dict) and t.get("tripId") else None


def _action_trip(action: dict, ctx: dict) -> Optional[dict]:
    """Viaje al que se aplica una accion del navegador: el activo del contexto o el que trae la accion."""
    trip = ctx.get("activeTrip") if isinstance(ctx, dict) else None
    if isinstance(trip, dict) and trip.get("tripId"):
        return trip
    if isinstance(action.get("tripId"), str) and action["tripId"]:
        return {"tripId": action["tripId"], "origin": action.get("origin"), "destination": action.get("destination"),
                "startDate": action.get("date"), "travelers": action.get("travelers")}
    return None


# --------------------------------------------------------------------------
# Entrypoint con STREAMING (SSE). Cada yield = un evento JSON.
#   {"type":"text","data":"..."}                      token de texto
#   {"type":"tool_start","name":"ask_flights","id":..} el agente empezo a usar una tool
#   {"type":"tool_result","name":...,"data":{...}}    resultado (JSON) de la tool
#   {"type":"plan","id","title","steps":[...]}        arranca una LINEA DE TIEMPO (pasos pendientes)
#   {"type":"step","plan","id","status","detail",...} un paso cambia (running / done / error / skipped)
#   {"type":"plan_done","plan","ok","ms"}             la linea de tiempo termino
#   {"type":"suggestions","items":[...]}              acciones sugeridas (botones)
#   {"type":"ping"} / {"type":"error","message":"..."} / {"type":"done"}
# --------------------------------------------------------------------------
@app.entrypoint
async def invoke(payload, context):
    prompt = (payload or {}).get("prompt", "")
    if not isinstance(prompt, str) or not prompt.strip():
        yield {"type": "error", "message": "prompt vacio"}
        yield {"type": "done"}
        return
    user_id = str(payload.get("userId") or "demo-user")[:64]
    session_id = str(payload.get("sessionId") or getattr(context, "session_id", None) or uuid.uuid4())
    ctx = payload.get("context") if isinstance(payload.get("context"), dict) else {}
    history = payload.get("history")
    action = payload.get("action") if isinstance(payload.get("action"), dict) else None

    yield {"type": "start", "sessionId": session_id, "userId": user_id}
    names: dict = {}
    started: set = set()
    try:
        # A) Boton "Reservar" del navegador: accion explicita, flujo determinista.
        if action and action.get("type") == "reserve_flight":
            flight, trip = _clean_flight(action.get("flight")), _action_trip(action, ctx)
            if flight and trip:
                async for ev in _reserve_turn(flight, trip, user_id):
                    yield ev
            else:
                yield {"type": "text", "data": "Para reservar necesito un viaje guardado y un vuelo elegido. Planifica el viaje primero."}
            yield {"type": "done"}
            return

        # A2) Boton "Eliminar" del navegador (una reserva concreta o todas).
        if action and action.get("type") == "delete_reservation":
            sel = ({"all": True} if action.get("all") else
                   {"reservationId": str(action.get("reservationId") or "")[:80]} if action.get("reservationId") else None)
            if sel:
                tid = str(action.get("tripId") or "")[:80]
                async for ev in _delete_turn(prompt, {"tripId": tid} if tid else _active_trip(ctx), user_id, sel):
                    yield ev
            else:
                yield {"type": "text", "data": "No entendí cuál reserva eliminar. Dime cuál (por ejemplo «elimina la reserva del vuelo»)."}
            yield {"type": "done"}
            return

        greeting = _is_greeting(prompt)

        # B0) "Elimina la reserva ..." escrito a mano (o la respuesta a "cual elimino?"): flujo determinista.
        if not greeting and (_is_delete_reservation_request(prompt) or _is_delete_followup(prompt, history)):
            async for ev in _delete_turn(prompt, _active_trip(ctx), user_id):
                yield ev
            yield {"type": "done"}
            return

        # B) "Reserva el vuelo mas barato" escrito a mano, con vuelos y viaje ya en pantalla.
        nl = None if greeting else _reserve_flight_request(prompt, ctx)
        if nl:
            async for ev in _reserve_turn(nl[0], nl[1], user_id):
                yield ev
            yield {"type": "done"}
            return

        # C) Planear un viaje: codigo conduce los pasos; si faltan datos, pregunta el agente (sin Tools).
        plan_req = (not greeting) and _is_plan_request(prompt, history, ctx)
        missing = [] if greeting else _missing_trip_info(prompt, history, {} if plan_req else ctx)
        if missing:
            print(f"[agent] faltan datos del viaje: {missing}")
        if not greeting and not plan_req:
            upd = _update_request(prompt, history, ctx)
            if upd is not None:
                async for ev in _update_turn(upd, prompt, history, ctx, user_id, session_id, names, started):
                    yield ev
                yield {"type": "done"}
                return
        if plan_req and missing:
            # La pregunta la escribe el CODIGO (no el modelo): asi siempre lleva "me faltan" y la siguiente respuesta
            # del usuario entra al plan con linea de tiempo en vez de caer en el agente libre.
            yield {"type": "text", "data": "Para armarlo me faltan: " + _join_es(missing).replace("cuantas", "cuántas")
                   + ". Cuéntamelo (por ejemplo: «salgo de Bogotá, somos 2 y tenemos 5 millones») y lo armo enseguida."}
            yield {"type": "done"}
            return
        if plan_req:
            async for ev in _plan_turn(prompt, history, ctx, user_id, session_id, names, started):
                yield ev
            yield {"type": "done"}
            return

        # D) Todo lo demas (saludos, pedir datos, cambiar, eliminar, consultar...): agente libre con Tools.
        agent = build_agent(session_id, user_id, ctx, history, greeting_only=greeting, missing=missing)
        current = prompt
        for attempt in range(3):          # el 2.o y 3.er intento solo ocurren si el agente dejo el trabajo a medias
            async for ev in _stream_turn(agent, current, names, started):
                yield ev
            if attempt < 2 and not greeting and not missing and _looks_unfinished(agent):
                print(f"[agent] turno inconcluso; reintento automatico {attempt + 1}/2")
                current = _NUDGE
                yield {"type": "text", "data": "\n\n"}
                continue
            break
    except Exception as exc:
        yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
    yield {"type": "done"}


if __name__ == "__main__":
    app.run()
