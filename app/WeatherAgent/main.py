# app/WeatherAgent/main.py  (v3)
#
# Weather Agent (A2A). Consulta el clima MEDIANTE UN SERVIDOR MCP (weather_mcp_server.py,
# transporte stdio, datos reales de Open-Meteo), igual que Flights usa el MCP de Kiwi.
# Responde SOLO JSON para que el frontend lo pinte como tarjetas.
# Incluye GET /ping (AgentCore Runtime exige HTTP 200).

import os
import sys
import json
import uuid
from strands import Agent, tool
from strands.multiagent.a2a import A2AServer
from strands.tools.mcp.mcp_client import MCPClient
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from starlette.responses import JSONResponse

BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # llm_provider.py vive junto a main.py
from llm_provider import build_model  # Bedrock por defecto; LLM_PROVIDER=openai_compat usa una API externa
SERVER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "weather_mcp_server.py")

# El servidor MCP corre como subproceso. En AgentCore, sys.executable puede apuntar al binario real de
# Python (".../bin/real/python3.14"), que NO arranca solo porque no encuentra libpython. Por eso el
# interprete y el entorno se eligen en tiempo de ejecucion, probando candidatos (ver _pick_python).
_mcp_python = None


def _python_env() -> dict:
    """Entorno del subproceso: el del proceso actual + las carpetas donde vive libpython."""
    env = dict(os.environ)
    if os.name == "posix":
        import sysconfig
        real = os.path.dirname(os.path.realpath(sys.executable))
        dirs = [sysconfig.get_config_var("LIBDIR"), os.path.join(sys.base_prefix, "lib"),
                os.path.join(real, "..", "lib"), os.path.join(real, "..", "..", "lib"), real]
        dirs = [os.path.normpath(d) for d in dirs if d and os.path.isdir(d)]
        current = [env["LD_LIBRARY_PATH"]] if env.get("LD_LIBRARY_PATH") else []
        env["LD_LIBRARY_PATH"] = ":".join(dict.fromkeys(dirs + current))
    return env


def _python_candidates() -> list:
    import shutil
    exe = sys.executable
    folder, name = os.path.split(exe)
    found = [exe]
    if os.path.basename(folder) == "real":                      # el lanzador de AgentCore vive un nivel arriba
        found.append(os.path.join(os.path.dirname(folder), name))
    found += [shutil.which(name), getattr(sys, "_base_executable", None), os.path.join(sys.base_prefix, "bin", name),
              shutil.which("python3"), shutil.which("python")]
    return list(dict.fromkeys(c for c in found if c and os.path.exists(c)))


def _pick_python() -> str:
    """Devuelve el primer interprete capaz de arrancar el servidor MCP (lo comprueba con --probe)."""
    global _mcp_python
    if _mcp_python:
        return _mcp_python
    import subprocess
    env, notes = _python_env(), []
    for cand in _python_candidates():
        try:
            r = subprocess.run([cand, SERVER_PATH, "--probe"], capture_output=True, text=True, timeout=30, env=env)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{cand}: {type(exc).__name__}: {exc}")
            continue
        if r.returncode == 0 and "PROBE_OK" in r.stdout:
            print(f"[weather] interprete MCP elegido: {cand} ({r.stdout.strip()})", file=sys.stderr, flush=True)
            _mcp_python = cand
            return cand
        notes.append(f"{cand}: codigo={r.returncode} {r.stderr.strip()[-220:]!r}")
    detail = " || ".join(notes) or "no hay interpretes candidatos"
    print(f"[weather] diagnostico del servidor MCP: ningun interprete arranco -> {detail}", file=sys.stderr, flush=True)
    raise RuntimeError(f"ningun interprete pudo arrancar el servidor MCP ({detail[:300]})")


weather_mcp = MCPClient(
    lambda: stdio_client(
        StdioServerParameters(command=_mcp_python or sys.executable, args=[SERVER_PATH], env=_python_env())
    )
)
_mcp_started = False
_mcp_failures = 0


def _root_cause(exc: BaseException) -> str:
    """Las ExceptionGroup de anyio esconden la causa real dentro de .exceptions."""
    parts, stack = [], [exc]
    while stack:
        e = stack.pop()
        subs = getattr(e, "exceptions", None)
        if subs:
            stack.extend(subs)
        else:
            parts.append(f"{type(e).__name__}: {e}")
        if e.__cause__ is not None:
            stack.append(e.__cause__)
    return " | ".join(parts[:3]) or str(exc)


def _ensure_mcp_started() -> None:
    """Lanza el servidor MCP la primera vez que se necesita, no al importar."""
    global _mcp_started
    if not _mcp_started:
        weather_mcp.start()
        _mcp_started = True


def _via_mcp(city: str, start_date: str, end_date: str) -> dict:
    _pick_python()
    _ensure_mcp_started()
    result = weather_mcp.call_tool_sync(
        tool_use_id=str(uuid.uuid4()),
        name="get_forecast",
        arguments={"city": city, "start_date": start_date, "end_date": end_date},
    )
    if result.get("status") != "success":
        raise RuntimeError(f"el MCP de clima devolvio un error: {result}")
    texts = [c.get("text", "") for c in result.get("content", []) if "text" in c]
    return json.loads(" ".join(texts))


@tool
def get_forecast(city: str, start_date: str, end_date: str = "") -> dict:
    """
    Clima REAL de una ciudad entre dos fechas, consultado en el MCP de clima
    (pronostico o, si la fecha esta lejos, el historico de esas fechas el ano anterior).

    Args:
        city: Ciudad (ej. "Cartagena", "Bogota").
        start_date: Fecha inicial, YYYY-MM-DD o dd/mm/yyyy.
        end_date: Fecha final (opcional; si se omite, un solo dia).
    """
    global _mcp_failures
    if _mcp_failures < 2:
        try:
            data = _via_mcp(city, start_date, end_date)
            data.setdefault("via", "mcp")
            return data
        except Exception as exc:  # noqa: BLE001
            _mcp_failures += 1
            print(f"[weather] MCP stdio fallo ({_mcp_failures}/2): {_root_cause(exc)}", file=sys.stderr, flush=True)
    # Respaldo: la misma logica, sin pasar por MCP, para que el usuario siempre reciba el clima.
    try:
        from weather_core import forecast
        data = forecast(city, start_date, end_date)
        data["via"] = "directo (el servidor MCP no arranco)"
        return data
    except Exception as exc:  # noqa: BLE001
        return {"city": city, "error": f"No se pudo consultar el clima: {_root_cause(exc)}"}


SYSTEM_PROMPT = (
    "Eres un agente de clima. Usa SIEMPRE la tool get_forecast con la ciudad y las "
    "fechas pedidas. Tu respuesta final debe ser UNICAMENTE un objeto JSON valido, "
    "sin texto antes ni despues y sin markdown, con esta forma exacta: "
    '{"city": "...", "source": "pronostico|historico_ano_anterior", '
    '"summary": "una frase corta en espanol", "days": [ ...los dias que devolvio la tool... ]}. '
    "Si la tool devuelve un error, responde {\"city\": \"...\", \"error\": \"...\"}. "
    "El mensaje trae la ciudad y las fechas: llama a get_forecast UNA vez con esos datos. "
    "Un nombre como 'Santa Marta, Magdalena' es UNA sola ciudad. "
    "Nunca pidas aclaraciones ni escribas etiquetas <thinking>."
)


def create_agent(context_id: str) -> Agent:
    return Agent(
        name="Weather Agent",
        description="Consulta el clima real (pronostico o historico) de una ciudad usando un servidor MCP de clima.",
        system_prompt=SYSTEM_PROMPT,
        model=build_model(BEDROCK_MODEL_ID),
        tools=[get_forecast],
        callback_handler=None,
    )


a2a_server = A2AServer(agent_factory=create_agent, host="0.0.0.0", port=9000)

# App Starlette del A2A server + /ping exigido por AgentCore Runtime.
app = a2a_server.to_starlette_app()


async def ping(request):
    return JSONResponse({"status": "Healthy"})


app.add_route("/ping", ping, methods=["GET"])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9000)
