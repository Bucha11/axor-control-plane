"""Demo-mode mock tools (spec decision #2): web_search + a minimal MCP server.

Zero-creds first contact: the user points their agent at the proxy, the proxy
points these tools at itself. Deterministic canned responses — the demo must
be reproducible every time (spec section 4).
"""
from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

_SEARCH_RESULTS = [
    {
        "title": "Quarterly rates overview",
        "url": "https://example.org/rates",
        "snippet": "Central bank rates held steady this quarter.",
    },
    {
        "title": "Market summary",
        "url": "https://example.org/markets",
        "snippet": "Bond yields moved 12bp on the quarter.",
    },
]


async def web_search(request: Request) -> Response:
    q = request.query_params.get("q", "")
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:  # malformed body: fall back to the query param
            body = {}
        q = body.get("q", body.get("query", q))
    return JSONResponse({"query": q, "results": _SEARCH_RESULTS})


_MCP_TOOLS = [
    {"name": "web_search", "description": "Search the (mock) web",
     "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}},
    {"name": "get_weather", "description": "Deterministic demo weather",
     "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}}},
]


async def mcp_tool(request: Request) -> Response:
    """A real (minimal) MCP server over streamable HTTP: answers the client
    handshake (initialize → notifications/initialized → tools/list) and
    tools/call — so MCP onboarding/discovery is demonstrable with zero creds,
    against the same deterministic canned data as the plain mock tools."""
    try:
        payload = await request.json() if request.method == "POST" else {}
    except Exception:
        payload = {}
    method = payload.get("method")
    rpc_id = payload.get("id", 1)

    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0", "id": rpc_id,
            "result": {
                "protocolVersion": payload.get("params", {}).get(
                    "protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "axor-mock-mcp", "version": "0.1"},
            },
        })
    if method == "notifications/initialized":
        return Response(status_code=202)
    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0", "id": rpc_id, "result": {"tools": _MCP_TOOLS},
        })
    if method == "tools/call":
        name = payload.get("params", {}).get("name", "")
        text = (
            json.dumps(_SEARCH_RESULTS) if name == "web_search"
            else "sunny, 21°C (deterministic demo weather)" if name == "get_weather"
            else f"unknown tool {name!r}"
        )
        return JSONResponse({
            "jsonrpc": "2.0", "id": rpc_id,
            "result": {"content": [{"type": "text", "text": text}],
                       "isError": name not in {t["name"] for t in _MCP_TOOLS}},
        })
    # Anything else: echo envelope (back-compat with the pre-MCP mock).
    return JSONResponse({
        "jsonrpc": "2.0", "id": rpc_id,
        "result": {
            "content": [{"type": "text",
                         "text": "mock MCP tool response (demo-mode)"}],
            "isError": False,
        },
    })


def mock_tools_app() -> Starlette:
    return Starlette(routes=[
        Route("/web_search", web_search, methods=["GET", "POST"]),
        Route("/mcp", mcp_tool, methods=["GET", "POST"]),
    ])
