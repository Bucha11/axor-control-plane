"""A real (minimal) stdio MCP server — `python -m axor_proxy.stdio_mock`.

The stdio twin of mock_tools.mcp_tool: same handshake, same deterministic
canned data, newline-delimited JSON over stdin/stdout (MCP stdio transport).
Exists so the stdio gateway is testable and demonstrable with zero creds —
and doubles as a reference for what the gateway expects from a server.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from axor_proxy.mock_tools import _MCP_TOOLS, _SEARCH_RESULTS


def handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    """One request → one response; notifications → None."""
    method = msg.get("method")
    rpc_id = msg.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": rpc_id,
            "result": {
                "protocolVersion": msg.get("params", {}).get(
                    "protocolVersion", "2025-03-26"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "axor-mock-mcp-stdio", "version": "0.1"},
            },
        }
    if isinstance(method, str) and method.startswith("notifications/"):
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": _MCP_TOOLS}}
    if method == "tools/call":
        name = msg.get("params", {}).get("name", "")
        text = (
            json.dumps(_SEARCH_RESULTS) if name == "web_search"
            else "sunny, 21°C (deterministic demo weather)" if name == "get_weather"
            else f"unknown tool {name!r}"
        )
        return {
            "jsonrpc": "2.0", "id": rpc_id,
            "result": {"content": [{"type": "text", "text": text}],
                       "isError": name not in {t["name"] for t in _MCP_TOOLS}},
        }
    return {
        "jsonrpc": "2.0", "id": rpc_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> int:
    # MCP servers log to stderr, never stdout — stdout is the protocol channel.
    print("axor-mock-mcp-stdio ready", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            print(json.dumps(resp, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
