"""El clima SIEMPRE debe responder: por MCP si arranca y, si no, directo (misma logica, sin MCP)."""
import io
import os
import sys
import importlib.util

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
HERE = os.path.dirname(os.path.abspath(__file__))
WA = os.path.join(HERE, "..", "app", "WeatherAgent")
sys.path.insert(0, WA)


def omitir_si_limite(*resultados):
    """La API gratuita de Open-Meteo limita por IP (429). Eso no es un fallo del codigo: se omite la prueba."""
    for r in resultados:
        if "429" in str(r.get("error", "")):
            print("OMITIDA: Open-Meteo respondio 429 (limite de peticiones). Repite en unos minutos.")
            sys.exit(0)


def load():
    spec = importlib.util.spec_from_file_location("weather_main", os.path.join(WA, "main.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# 1) camino normal por MCP
m = load()
r = m.get_forecast(city="Cartagena", start_date="2026-12-04", end_date="2026-12-08")
if "429" in str(r.get("error", "")):          # la API gratuita limita por IP; no es un fallo del codigo
    print("OMITIDA: Open-Meteo respondio 429 (limite de peticiones). Repite en unos minutos.")
    sys.exit(0)
print("MCP ->", r.get("via"), len(r.get("days", [])), "dias")
assert r.get("via") == "mcp" and len(r["days"]) == 5, r

# 2) el servidor MCP no arranca -> respaldo directo, con la causa real en el log
m = load()
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands.tools.mcp.mcp_client import MCPClient
m.weather_mcp = MCPClient(lambda: stdio_client(StdioServerParameters(command=sys.executable, args=["/no/existe.py"])))
m.SERVER_PATH = "/no/existe.py"
real, sys.stderr = sys.stderr, io.StringIO()
try:
    r1 = m.get_forecast(city="Cartagena", start_date="2026-12-04", end_date="2026-12-08")
    r2 = m.get_forecast(city="Bogota", start_date="2026-12-04")
    r3 = m.get_forecast(city="Medellin", start_date="2026-12-04")
    log = sys.stderr.getvalue()
finally:
    sys.stderr = real
print("respaldo ->", r1.get("via"), len(r1.get("days", [])), "dias")
omitir_si_limite(r1, r2, r3)
assert "directo" in r1["via"] and len(r1["days"]) == 5, r1
assert "directo" in r2["via"] and "directo" in r3["via"]
assert "[weather] MCP stdio fallo (1/2)" in log and "diagnostico del servidor MCP" in log, log
assert "codigo=2" in log                          # el diagnostico incluye lo que dijo cada interprete
assert m._mcp_failures == 2                      # tras 2 fallos ya no reintenta el MCP
print("OK: el clima responde aunque el MCP falle")

# 2b) caso real de AgentCore: sys.executable es ".../bin/real/python3.14" y NO arranca (falta libpython);
#     el lanzador de la carpeta superior si funciona. El agente debe elegirlo y usar el MCP de verdad.
import stat, tempfile
tmp = tempfile.mkdtemp()
os.makedirs(os.path.join(tmp, "bin", "real"))
broken = os.path.join(tmp, "bin", "real", "python3.14")
with open(broken, "w") as f:
    f.write("#!/bin/sh\necho \"%s: error while loading shared libraries: libpython3.14.so.1.0: cannot open shared object file\" >&2\nexit 127\n" % broken)
launcher = os.path.join(tmp, "bin", "python3.14")
with open(launcher, "w") as f:
    f.write('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
for fp in (broken, launcher):
    os.chmod(fp, os.stat(fp).st_mode | stat.S_IEXEC)
real_exe = sys.executable
sys.executable = broken
try:
    m = load()
    cands = m._python_candidates()
    assert cands[0] == broken and cands[1] == launcher, cands
    real, sys.stderr = sys.stderr, io.StringIO()
    try:
        r = m.get_forecast(city="Cartagena", start_date="2026-12-04", end_date="2026-12-08")
        log2 = sys.stderr.getvalue()
    finally:
        sys.stderr = real
finally:
    sys.executable = real_exe
omitir_si_limite(r)
print("AgentCore simulado ->", r.get("via"), "| interprete:", m._mcp_python)
assert r.get("via") == "mcp" and m._mcp_python == launcher, (r, m._mcp_python, log2)
assert m._mcp_failures == 0

# 3) ciudad desconocida -> error util, no excepcion
r4 = m.get_forecast(city="Ciudad Inventada XYZ", start_date="2026-12-04")
print("ciudad rara ->", {k: r4[k] for k in r4 if k in ("error", "via")})
