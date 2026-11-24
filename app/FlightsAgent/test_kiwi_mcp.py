# test_kiwi_mcp.py
#
# Prueba de la llamada REAL a la tool search-flight, para ver la forma exacta
# del resultado (MCPToolResult) antes de escribir el wrapper final.
#
# Uso (dentro de app/FlightsAgent):
#   uv run python test_kiwi_mcp.py

import uuid
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client

KIWI_MCP_URL = "https://mcp.kiwi.com"

mcp = MCPClient(lambda: streamable_http_client(KIWI_MCP_URL))

with mcp:
    tools = mcp.list_tools_sync()
    print(f"Conectado a {KIWI_MCP_URL}. Tools encontradas: {len(tools)}")

    result = mcp.call_tool_sync(
        tool_use_id=str(uuid.uuid4()),
        name="search-flight",
        arguments={
            "flyFrom": "BAQ",
            "flyTo": "BOG",
            "departureDate": "25/12/2026",
            "currency": "USD",
        },
    )
    print("\n--- tipo del resultado ---")
    print(type(result))
    print("\n--- dir() del resultado ---")
    print([x for x in dir(result) if not x.startswith("_")])
    print("\n--- repr truncado (primeros 1500 chars) ---")
    print(repr(result)[:1500])