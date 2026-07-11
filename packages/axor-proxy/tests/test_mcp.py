"""MCP onboarding (launch-readiness §1): discovery handshake against the
built-in mock MCP server, runtime registration, and the `server:tool`
observation granularity for proxied tools/call bodies."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from axor_proxy.app import ProxyState, create_app
from axor_proxy.mcp import sniff_rpc_call


@pytest.fixture
def proxy(tmp_path: Path) -> httpx.AsyncClient:
    """Proxy whose outbound client dials the proxy app itself, so the built-in
    /mock/mcp is reachable as a discovery target and as a tool upstream."""
    state = ProxyState(tools={}, trace_dir=tmp_path, client=None)
    app = create_app(state)
    loop_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )
    state.client = loop_client
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )
    client.proxy_state = state  # type: ignore[attr-defined]
    return client


async def test_discover_registers_the_server_and_lists_tools(
    proxy: httpx.AsyncClient,
) -> None:
    resp = await proxy.post("/axor/mcp/discover", json={
        "url": "http://proxy.test/mock/mcp",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["server"] == "axor-mock-mcp"
    assert body["registered"] == "axor-mock-mcp"
    assert body["proxied_base"] == "/t/axor-mock-mcp/"
    names = {t["name"] for t in body["tools"]}
    assert names == {"web_search", "get_weather"}
    # The server is now a proxied tool endpoint.
    state = proxy.proxy_state  # type: ignore[attr-defined]
    assert state.tools["axor-mock-mcp"] == "http://proxy.test/mock/mcp"


async def test_discover_rejects_missing_url_and_dead_server(
    proxy: httpx.AsyncClient,
) -> None:
    assert (await proxy.post("/axor/mcp/discover", json={})).status_code == 400
    dead = await proxy.post("/axor/mcp/discover", json={
        "url": "http://proxy.test/mock/not-mcp",
    })
    assert dead.status_code == 502
    assert dead.json()["error"] == "mcp_discovery_failed"


async def test_proxied_tools_call_records_server_tool_granularity(
    proxy: httpx.AsyncClient,
) -> None:
    await proxy.post("/axor/mcp/discover", json={
        "url": "http://proxy.test/mock/mcp", "name": "demo_mcp",
    })
    run = (await proxy.post("/axor/runs", json={
        "scenario": "mcp", "faults": [], "node_id": "n1",
    })).json()

    call = await proxy.post("/t/demo_mcp/", json={
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "get_weather", "arguments": {"city": "Berlin"}},
    })
    assert call.status_code == 200
    assert "21°C" in call.json()["result"]["content"][0]["text"]

    # The observation names the inner tool, not just the endpoint.
    state = proxy.proxy_state  # type: ignore[attr-defined]
    trace = state.runs.get(run["run_id"]).recorder.path.read_text()
    lines = [json.loads(line) for line in trace.splitlines()]
    call_line = next(ln for ln in lines if ln["kind"] == "tool_call")
    assert call_line["payload"]["tool"] == "demo_mcp:get_weather"
    assert call_line["payload"]["rpc"] == {"method": "tools/call", "tool": "get_weather"}


def test_sniff_rpc_call_never_raises() -> None:
    assert sniff_rpc_call(b"") is None
    assert sniff_rpc_call(b"not json") is None
    assert sniff_rpc_call(b'{"jsonrpc":"1.0","method":"x"}') is None
    assert sniff_rpc_call(b'{"jsonrpc":"2.0","method":"tools/list"}') == {
        "method": "tools/list",
    }
