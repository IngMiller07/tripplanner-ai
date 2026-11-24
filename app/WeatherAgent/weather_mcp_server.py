# app/WeatherAgent/weather_mcp_server.py
#
# Servidor MCP de clima (transporte stdio). Expone la tool "get_forecast" con datos
# REALES de Open-Meteo (gratis, sin API key): pronostico si la fecha esta dentro de
# ~16 dias, o el historico de esas mismas fechas el ano anterior.
# El Weather Agent (A2A) lo lanza como subproceso y lo consume como cliente MCP.
# La logica vive en weather_core.py (sin dependencias de MCP).

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from weather_core import forecast

try:
    from mcp.server.mcpserver import MCPServer  # mcp 2.x
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

mcp = MCPServer("weather")


@mcp.tool()
def get_forecast(city: str, start_date: str, end_date: str = "") -> dict:
    """
    Clima REAL de una ciudad entre dos fechas (pronostico o, si la fecha esta
    lejos, el historico de esas mismas fechas el ano anterior).

    Args:
        city: Ciudad (ej. "Cartagena", "Bogota").
        start_date: Fecha inicial, YYYY-MM-DD o dd/mm/yyyy.
        end_date: Fecha final (opcional; si se omite, un solo dia).
    """
    return forecast(city, start_date, end_date)


if __name__ == "__main__":
    if "--probe" in sys.argv:      # diagnostico: importa MCP, crea el servidor y registra la tool; no arranca el transporte
        import importlib.metadata as _md
        print("PROBE_OK python", sys.version.split()[0], "mcp", _md.version("mcp"), "exe", sys.executable)
        sys.exit(0)
    mcp.run(transport="stdio")
