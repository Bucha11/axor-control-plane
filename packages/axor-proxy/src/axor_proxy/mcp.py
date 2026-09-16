"""MCP (Model Context Protocol) discovery — the 2026 tool layer speaks MCP, so
onboarding must accept an MCP server, not only bare HTTP endpoints.

Scope (launch-readiness §1): HTTP-transported MCP servers (streamable HTTP).
`discover()` performs the client handshake — `initialize` →
`notifications/initialized` → `tools/list` — and returns the server name and its
tool inventory. stdio-transported servers go through the local gateway in
`stdio_mcp.py` (same handshake, subprocess transport).

The proxy stays observe-only: discovery is read-only JSON-RPC, and a registered
MCP server is proxied exactly like any other tool endpoint — auth passthrough,
fault injection, observation. The only MCP-specific enrichment is in the
observation payload: a `tools/call` body names the inner tool, so the trace
records `server:tool` granularity instead of one opaque endpoint.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

PROTOCOL_VERSION = "2025-03-26"


class McpError(Exception):
    """Discovery failed: unreachable, not JSON-RPC, or a JSON-RPC error."""


def _parse_rpc_body(resp: httpx.Response) -> dict[str, Any]:
    """A streamable-HTTP server may answer application/json or a single-event
    text/event-stream — accept both."""
    ctype = resp.headers.get("content-type", "")
    if ctype.startswith("text/event-stream"):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise McpError("SSE response carried no data event")
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise McpError(f"not a JSON-RPC response: {exc}") from exc


def _result(body: dict[str, Any], what: str) -> dict[str, Any]:
    if "error" in body:
        err = body["error"]
        raise McpError(f"{what} failed: {err.get('message', err)}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise McpError(f"{what}: malformed result")
    return result


async def discover(
    url: str, client: httpx.AsyncClient, rpc_timeout: float = 10.0,
) -> dict[str, Any]:
    """Handshake with an HTTP MCP server and list its tools.

    Returns {"server": name, "protocol_version": v, "tools": [{name, description}]}.
    Raises McpError on anything that isn't a healthy MCP server.
    """
    headers = {
        "content-type": "application/json",
        # Streamable-HTTP servers may answer either; advertise both.
        "accept": "application/json, text/event-stream",
    }

    async def rpc(payload: dict[str, Any]) -> httpx.Response:
        try:
            return await client.post(url, json=payload, headers=headers, timeout=rpc_timeout)
        except httpx.HTTPError as exc:
            raise McpError(f"server unreachable: {exc}") from exc

    init = await rpc({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "axor-proxy", "version": "0.1"},
        },
    })
    if init.status_code >= 400:
        raise McpError(f"initialize: HTTP {init.status_code}")
    init_result = _result(_parse_rpc_body(init), "initialize")
    # Spec: echo the session id the server assigned, if any.
    session = init.headers.get("mcp-session-id")
    if session:
        headers["mcp-session-id"] = session

    # Fire-and-forget per spec; a server that 4xxes the notification is still
    # usable for discovery.
    await rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})

    listed = await rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    if listed.status_code >= 400:
        raise McpError(f"tools/list: HTTP {listed.status_code}")
    tools_result = _result(_parse_rpc_body(listed), "tools/list")

    server_info = init_result.get("serverInfo", {})
    return {
        "server": server_info.get("name", "mcp-server"),
        "protocol_version": init_result.get("protocolVersion", PROTOCOL_VERSION),
        "tools": [
            {"name": t.get("name", ""), "description": t.get("description", "")}
            for t in tools_result.get("tools", [])
            if isinstance(t, dict)
        ],
    }


def sniff_rpc_call(body: bytes) -> dict[str, Any] | None:
    """Best-effort observation enrichment: if a proxied request body is a
    JSON-RPC call, name the method (and the inner tool for tools/call) so the
    trace records `server:tool` granularity. Never raises — observation only."""
    if not body or body[:1] not in (b"{",):
        return None
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict) or parsed.get("jsonrpc") != "2.0":
        return None
    method = parsed.get("method")
    if not isinstance(method, str):
        return None
    out: dict[str, Any] = {"method": method}
    if method == "tools/call":
        params = parsed.get("params")
        if isinstance(params, dict) and isinstance(params.get("name"), str):
            out["tool"] = params["name"]
            # The call's arguments, for the governor to evaluate against the
            # run's manifests. They are NOT recorded — the trace keeps
            # observations (size, sha256, the verdict), never the body. Only
            # `tools/call` carries them, which is why a governed run says out
            # loud which of its calls the governor could not see.
            args = params.get("arguments")
            if isinstance(args, dict):
                out["arguments"] = args
    return out
