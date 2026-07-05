"""Demo-mode mock tools (spec decision #2): web_search + a generic MCP tool.

Zero-creds first contact: the user points their agent at the proxy, the proxy
points these tools at itself. Deterministic canned responses — the demo must
be reproducible every time (spec section 4).
"""
from __future__ import annotations

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
        body = await request.json()
        q = body.get("q", body.get("query", q))
    return JSONResponse({"query": q, "results": _SEARCH_RESULTS})


async def mcp_tool(request: Request) -> Response:
    """Generic MCP-shaped tool: echoes a tools/call result envelope."""
    payload = await request.json() if request.method == "POST" else {}
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": payload.get("id", 1),
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
