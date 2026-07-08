"""stdio-MCP gateway: discovery spawns a REAL local process (the stdio twin of
the mock MCP server), proxied calls flow through the same fault/observation
pipeline as HTTP tools, and a dead process is a 502 — never a hang."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from axor_proxy.app import ProxyState, create_app
from axor_proxy.stdio_mcp import StdioMcpServer

MOCK_CMD = [sys.executable, "-m", "axor_proxy.stdio_mock"]


@pytest.fixture
async def proxy(tmp_path: Path) -> httpx.AsyncClient:
    state = ProxyState(tools={}, trace_dir=tmp_path, client=None)
    app = create_app(state)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )
    client.proxy_state = state  # type: ignore[attr-defined]
    yield client
    # ASGITransport does not run lifespan; close spawned servers explicitly.
    for upstream in state.tools.values():
        if isinstance(upstream, StdioMcpServer):
            await upstream.close()
    await client.aclose()


async def _discover(proxy: httpx.AsyncClient, **extra: object) -> dict:
    resp = await proxy.post(
        "/axor/mcp/discover", json={"command": MOCK_CMD, **extra}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_discover_spawns_and_registers_a_stdio_server(
    proxy: httpx.AsyncClient,
) -> None:
    body = await _discover(proxy)
    assert body["server"] == "axor-mock-mcp-stdio"
    assert body["transport"] == "stdio"
    assert {t["name"] for t in body["tools"]} == {"web_search", "get_weather"}
    state = proxy.proxy_state  # type: ignore[attr-defined]
    server = state.tools["axor-mock-mcp-stdio"]
    assert isinstance(server, StdioMcpServer)
    assert server.alive


async def test_discover_accepts_a_command_string(proxy: httpx.AsyncClient) -> None:
    body = await _discover(proxy, command=" ".join(MOCK_CMD), name="strcmd")
    assert body["registered"] == "strcmd"


async def test_discover_rejects_a_broken_command(proxy: httpx.AsyncClient) -> None:
    resp = await proxy.post("/axor/mcp/discover", json={
        "command": ["/nonexistent/definitely-not-an-mcp-server"],
    })
    assert resp.status_code == 502
    assert resp.json()["error"] == "mcp_discovery_failed"


async def test_proxied_call_flows_and_records_stdio_observation(
    proxy: httpx.AsyncClient,
) -> None:
    await _discover(proxy, name="local")
    run = (await proxy.post("/axor/runs", json={
        "scenario": "stdio", "faults": [], "node_id": "n1",
    })).json()

    call = await proxy.post("/t/local/", json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "get_weather", "arguments": {"city": "Berlin"}},
    })
    assert call.status_code == 200
    assert "21°C" in call.json()["result"]["content"][0]["text"]

    state = proxy.proxy_state  # type: ignore[attr-defined]
    trace = state.runs.get(run["run_id"]).recorder.path.read_text()
    lines = [json.loads(line) for line in trace.splitlines()]
    call_line = next(ln for ln in lines if ln["kind"] == "tool_call")
    # server:tool granularity works across transports.
    assert call_line["payload"]["tool"] == "local:get_weather"
    result_line = next(ln for ln in lines if ln["kind"] == "tool_result")
    assert result_line["payload"]["transport"] == "stdio"
    assert result_line["payload"]["response_sha256"]


async def test_fault_injection_applies_to_stdio_tools(
    proxy: httpx.AsyncClient,
) -> None:
    await _discover(proxy, name="local")
    run = (await proxy.post("/axor/runs", json={
        "scenario": "stdio-fault", "node_id": "n1",
        "faults": [{"tool": "local", "mode": "silent_fail"}],
    })).json()

    call = await proxy.post("/t/local/", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "web_search", "arguments": {"q": "rates"}},
    })
    assert call.status_code == 200

    state = proxy.proxy_state  # type: ignore[attr-defined]
    trace = state.runs.get(run["run_id"]).recorder.path.read_text()
    kinds = [json.loads(line)["kind"] for line in trace.splitlines()]
    assert "fault_injected" in kinds


async def test_non_jsonrpc_body_is_a_400(proxy: httpx.AsyncClient) -> None:
    await _discover(proxy, name="local")
    await proxy.post("/axor/runs", json={
        "scenario": "s", "faults": [], "node_id": "n1",
    })
    resp = await proxy.post("/t/local/", content=b"plain text")
    assert resp.status_code == 400
    assert resp.json()["error"] == "jsonrpc_required"


async def test_dead_server_is_an_honest_502(proxy: httpx.AsyncClient) -> None:
    await _discover(proxy, name="local")
    await proxy.post("/axor/runs", json={
        "scenario": "s", "faults": [], "node_id": "n1",
    })
    state = proxy.proxy_state  # type: ignore[attr-defined]
    await state.tools["local"].close()

    resp = await proxy.post("/t/local/", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
    })
    assert resp.status_code == 502
    assert resp.json()["error"] == "upstream_unreachable"


async def test_notification_returns_202_without_body(
    proxy: httpx.AsyncClient,
) -> None:
    await _discover(proxy, name="local")
    await proxy.post("/axor/runs", json={
        "scenario": "s", "faults": [], "node_id": "n1",
    })
    resp = await proxy.post("/t/local/", json={
        "jsonrpc": "2.0", "method": "notifications/cancelled",
    })
    assert resp.status_code == 202
    assert resp.content == b""


async def test_rediscover_same_name_replaces_the_old_process(
    proxy: httpx.AsyncClient,
) -> None:
    await _discover(proxy, name="local")
    state = proxy.proxy_state  # type: ignore[attr-defined]
    first = state.tools["local"]
    await _discover(proxy, name="local")
    second = state.tools["local"]
    assert first is not second
    assert not first.alive  # the replaced process was terminated, not leaked
    assert second.alive


async def test_stdio_registration_is_loopback_only(tmp_path: Path) -> None:
    """A non-loopback caller must not be able to make the proxy spawn a
    process — an exposed proxy port is not a remote-exec endpoint."""
    state = ProxyState(tools={}, trace_dir=tmp_path, client=None)
    app = create_app(state)
    remote = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.9", 4242)),
        base_url="http://proxy.test",
    )
    resp = await remote.post("/axor/mcp/discover", json={"command": MOCK_CMD})
    assert resp.status_code == 403
    assert resp.json()["error"] == "stdio_requires_loopback"
    # HTTP discovery is unaffected by the caller's address.
    assert "stdio" not in str(state.tools)
    await remote.aclose()


async def test_preflight_reports_stdio_liveness(proxy: httpx.AsyncClient) -> None:
    await _discover(proxy, name="local")
    ok = (await proxy.get("/axor/preflight")).json()
    assert ok["tools"]["local"] == {"ok": True, "transport": "stdio"}

    state = proxy.proxy_state  # type: ignore[attr-defined]
    await state.tools["local"].close()
    gone = (await proxy.get("/axor/preflight")).json()
    assert gone["tools"]["local"]["ok"] is False
