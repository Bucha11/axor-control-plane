"""Concurrent armed runs + trace retention (launch-readiness §1 P1)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest
from axor_proxy.app import ProxyState, create_app
from axor_proxy.runs import RunManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def upstream_app() -> Starlette:
    async def handler(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return Starlette(routes=[Route("/{path:path}", handler, methods=["GET", "POST"])])


@pytest.fixture
def proxy(tmp_path: Path) -> httpx.AsyncClient:
    state = ProxyState(
        tools={"web_search": "http://upstream.test/s"},
        trace_dir=tmp_path,
        client=httpx.AsyncClient(
            transport=httpx.ASGITransport(app=upstream_app()),
            base_url="http://upstream.test",
        ),
    )
    app = create_app(state)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )
    client.proxy_state = state  # type: ignore[attr-defined]
    return client


async def _arm(proxy: httpx.AsyncClient, node: str) -> str:
    r = await proxy.post("/axor/runs", json={
        "scenario": "s", "faults": [], "node_id": node,
    })
    return r.json()["run_id"]


async def test_two_runs_record_independently_via_header(
    proxy: httpx.AsyncClient,
) -> None:
    run_a = await _arm(proxy, "team-a")
    run_b = await _arm(proxy, "team-b")

    # Each caller names its run; traffic lands in the right trace.
    await proxy.get("/t/web_search/", headers={"X-Axor-Run": run_a})
    await proxy.get("/t/web_search/", headers={"X-Axor-Run": run_b})
    await proxy.get("/t/web_search/", headers={"X-Axor-Run": run_b})

    state = proxy.proxy_state  # type: ignore[attr-defined]
    calls_a = [json.loads(ln) for ln in
               state.runs.get(run_a).recorder.path.read_text().splitlines()
               if json.loads(ln)["kind"] == "tool_call"]
    calls_b = [json.loads(ln) for ln in
               state.runs.get(run_b).recorder.path.read_text().splitlines()
               if json.loads(ln)["kind"] == "tool_call"]
    assert len(calls_a) == 1 and len(calls_b) == 2

    # Claiming run A disarms only A: B keeps serving, A's header now 503s.
    await proxy.post(f"/axor/runs/{run_a}/claim", json={"text": "done"})
    assert (await proxy.get("/t/web_search/",
                            headers={"X-Axor-Run": run_a})).status_code == 503
    assert (await proxy.get("/t/web_search/",
                            headers={"X-Axor-Run": run_b})).status_code == 200


async def test_headerless_calls_use_most_recent_run_and_unknown_header_503s(
    proxy: httpx.AsyncClient,
) -> None:
    await _arm(proxy, "n1")
    run_recent = await _arm(proxy, "n2")
    assert (await proxy.get("/t/web_search/")).status_code == 200
    state = proxy.proxy_state  # type: ignore[attr-defined]
    trace = state.runs.get(run_recent).recorder.path.read_text()
    assert "tool_call" in trace  # landed in the most recent run
    # Naming a nonexistent run never falls back to somebody else's run.
    assert (await proxy.get("/t/web_search/",
                            headers={"X-Axor-Run": "run_nope"})).status_code == 503


def test_trace_retention_prunes_old_files(tmp_path: Path) -> None:
    mgr = RunManager(tmp_path, retention_days=7)
    old = tmp_path / "run_old.jsonl"
    old.write_text("{}\n")
    stale_mtime = time.time() - 8 * 86400
    os.utime(old, (stale_mtime, stale_mtime))
    fresh = tmp_path / "run_fresh.jsonl"
    fresh.write_text("{}\n")

    pruned = mgr.prune_traces()
    assert pruned == 1
    assert not old.exists() and fresh.exists()
    # Unset retention: never prunes.
    assert RunManager(tmp_path, retention_days=None).prune_traces() == 0
