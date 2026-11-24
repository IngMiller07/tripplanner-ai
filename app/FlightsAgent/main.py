# app/FlightsAgent/main.py
#
# Flights Agent (A2A). search_flights llama por dentro al servidor MCP real
# de Kiwi.com (sin API key) y devuelve vuelos reales.
#
# Dos detalles importantes para AgentCore Runtime:
# 1) La conexion al MCP es perezosa (lazy): se intenta en la primera busqueda
#    real, NO al importar el modulo.
# 2) AgentCore hace health check con GET /ping y ahora EXIGE HTTP 200
#    (antes toleraba 404). Strands A2AServer no trae /ping, asi que lo
#    agregamos nosotros a la app Starlette.

import os
import json
import uuid
import unicodedata
from strands import Agent, tool
from strands.multiagent.a2a import A2AServer
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
from starlette.responses import JSONResponse

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # llm_provider.py vive junto a main.py
from llm_provider import build_model  # Bedrock por defecto; LLM_PROVIDER=openai_compat usa una API externa
KIWI_MCP_URL = os.environ.get("KIWI_MCP_URL", "https://mcp.kiwi.com")

SYSTEM_PROMPT = (
    "Eres un agente de busqueda de vuelos. Usa SIEMPRE la tool 'search_flights' para "
    "buscar vuelos reales (no simulados). La fecha va en formato dd/mm/yyyy. "
    "Tu respuesta final debe ser UNICAMENTE un objeto JSON valido, sin texto antes ni "
    "despues y sin markdown, con esta forma exacta: "
    '{"origin": "...", "destination": "...", "date": "dd/mm/yyyy", "currency": "USD", '
    '"flights": [ ...los vuelos que devolvio la tool, sin inventar ninguno... ]}. '
    "Si la tool devuelve un error, responde {\"error\": \"...\"}. "
    "El mensaje trae el origen, el destino y la fecha: llama a search_flights UNA vez con esos tres datos. "
    "Un nombre como 'Barranquilla, Atlantico' es UNA sola ciudad (el departamento no es otro lugar). "
    "Nunca pidas aclaraciones ni escribas etiquetas <thinking>: si falta algun dato, responde {\"error\": \"falta origen, destino o fecha\"}."
)

# Codigos IATA de ciudades frecuentes (evita ambiguedad al buscar en Kiwi).
IATA = {
    "bogota": "BOG", "cartagena": "CTG", "medellin": "MDE", "cali": "CLO",
    "barranquilla": "BAQ", "santa marta": "SMR", "san andres": "ADZ",
    "pereira": "PEI", "bucaramanga": "BGA", "cucuta": "CUC", "leticia": "LET",
    "madrid": "MAD", "miami": "MIA", "cancun": "CUN", "lima": "LIM",
    "ciudad de mexico": "MEX", "buenos aires": "EZE", "nueva york": "JFK",
}


def _to_iata(place: str) -> str:
    key = unicodedata.normalize("NFKD", (place or "").lower().strip())
    key = "".join(c for c in key if not unicodedata.combining(c)).split(",")[0].strip()
    return IATA.get(key, place)


travel_mcp = MCPClient(lambda: streamable_http_client(KIWI_MCP_URL))
_mcp_started = False


def _ensure_mcp_started() -> None:
    """Conecta al MCP de Kiwi la primera vez que se necesita, no al arrancar."""
    global _mcp_started
    if not _mcp_started:
        travel_mcp.start()
        _mcp_started = True


def _parse_kiwi_itineraries(raw_text: str, limit: int = 5) -> list[dict]:
    """Convierte el JSON crudo de Kiwi en una lista corta y simple para el LLM."""
    try:
        data = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return []
    parsed = []
    for it in (data.get("itineraries") or [])[:limit]:
        outbound = it.get("outbound") or {}
        segments = outbound.get("segments") or []
        first_seg = segments[0] if segments else {}
        parsed.append(
            {
                "airline": first_seg.get("carrierName", "?"),
                "flight": first_seg.get("flightNumber", "?"),
                "depart": (outbound.get("departureTime") or "")[11:16],
                "arrive": (outbound.get("arrivalTime") or "")[11:16],
                "price_usd": it.get("price"),
                "stops": outbound.get("stops", 0),
            }
        )
    return parsed


@tool
def search_flights(origin: str, destination: str, date: str) -> list[dict]:
    """
    Busca vuelos reales entre dos ciudades o aeropuertos para una fecha dada,
    usando el servidor MCP de Kiwi.com (datos reales, no simulados).

    Args:
        origin: Origen. Codigo IATA, ciudad o aeropuerto (ej. "BAQ", "Barranquilla").
        destination: Destino. Codigo IATA, ciudad o aeropuerto (ej. "BOG", "Bogota").
        date: Fecha de salida en formato dd/mm/yyyy (ej. "25/12/2026").
    """
    try:
        _ensure_mcp_started()
    except Exception as exc:
        return [{"error": f"No se pudo conectar al MCP de vuelos: {exc}"}]

    try:
        result = travel_mcp.call_tool_sync(
            tool_use_id=str(uuid.uuid4()),
            name="search-flight",
            arguments={
                "flyFrom": _to_iata(origin),
                "flyTo": _to_iata(destination),
                "departureDate": date,
                "currency": "USD",
            },
        )
    except Exception as exc:
        return [{"error": f"No se pudo consultar el MCP de vuelos: {exc}"}]

    if result.get("status") != "success":
        return [{"error": f"El MCP de vuelos devolvio un error: {result}"}]

    texts = [c.get("text", "") for c in result.get("content", []) if "text" in c]
    itineraries = _parse_kiwi_itineraries(" ".join(texts))
    if not itineraries:
        return [{"error": "No se encontraron vuelos reales para esa ruta o fecha."}]
    return itineraries


def create_agent(context_id: str) -> Agent:
    return Agent(
        name="Flights Agent",
        description="Busca vuelos reales entre ciudades para una fecha dada, usando el servidor MCP de Kiwi.com.",
        system_prompt=SYSTEM_PROMPT,
        model=build_model(BEDROCK_MODEL_ID),
        tools=[search_flights],
        callback_handler=None,
    )


a2a_server = A2AServer(agent_factory=create_agent, host="0.0.0.0", port=9000)

# App Starlette del A2A server + endpoint /ping exigido por AgentCore Runtime.
app = a2a_server.to_starlette_app()


async def ping(request):
    return JSONResponse({"status": "Healthy"})


app.add_route("/ping", ping, methods=["GET"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9000)
