"""The demo surfaces on a hosted plane (QA run 2026-11-27).

QA-001: a demo node / tree spawned from the page must land in the workspace of
whoever spawned it — the proxy's own ingest key put it in the key's workspace,
so on a multi-tenant plane nobody ever saw it — and a refused upload must not
show the plane's internal address.
QA-002: the in-app experiment must finish on a proxy that guards its control
surface (AXOR_PROXY_TOKEN): its scripted agent claimed without the token.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_proxy.app import ProxyState, create_app
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

TOKEN = "proxy-control-token"


def _upstream() -> httpx.ASGITransport:
    async def handler(request: object) -> JSONResponse:
        return JSONResponse({"results": [{"content": "real doc"}]})
    return httpx.ASGITransport(
        app=Starlette(routes=[Route("/{path:path}", handler, methods=["GET", "POST"])]))


async def test_the_example_experiment_finishes_on_a_guarded_proxy(
        tmp_path: pathlib.Path) -> None:
    state = ProxyState(tools={"web_search": "http://upstream.test/s"}, trace_dir=tmp_path,
                       client=httpx.AsyncClient(transport=_upstream(), base_url="http://upstream.test"),
                       control_token=TOKEN)
    app = create_app(state)
    page = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy.test",
                             headers={"Authorization": f"Bearer {TOKEN}"})
    # the scripted agent dials the proxy itself, as an agent does: no token of its own
    state.self_base_url = "http://proxy.test"
    state.agent_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                           base_url="http://proxy.test")
    run_id = (await page.post("/axor/runs", json={
        "scenario": "tool-deprivation",
        "faults": [{"tool": "web_search", "mode": "silent_fail", "probability": 1.0}],
    })).json()["run_id"]
    r = await page.post(f"/axor/runs/{run_id}/simulate", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("error") != "unauthorized", body
    assert body["deviations"] == 1   # the claim reached the proxy and was audited
    assert (await page.get(f"/axor/runs/{run_id}")).json()["completed"] is True


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    seen: list[str | None] = []

    async def fake_node(backend_url: str, ingest_key: str | None = None, **_: object) -> dict:
        seen.append(ingest_key)

        async def idle() -> None:
            return None
        import asyncio
        return {"node_id": "gov_x", "run_id": "gov_x", "events": 1, "denials": 0,
                "ttl_seconds": 1.0, "_task": asyncio.ensure_future(idle())}

    async def fake_tree(backend_url: str, ingest_key: str | None = None, **_: object) -> dict:
        seen.append(ingest_key)
        return {"run_id": "tree_x"}

    monkeypatch.setattr("axor_proxy.governed.spawn_governed_node", fake_node)
    monkeypatch.setattr("axor_proxy.governed.spawn_governed_tree", fake_tree)
    return seen


def _proxy(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    state = ProxyState(tools={}, trace_dir=tmp_path, backend_url="http://backend:8400",
                       ingest_key="proxy-own-key")
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)),
                             base_url="http://proxy.test")


@pytest.mark.parametrize("route", ["/axor/governed/spawn", "/axor/governed/spawn-tree"])
async def test_a_demo_spawn_lands_in_the_spawners_workspace(
        tmp_path: pathlib.Path, spawned: list[str | None], route: str) -> None:
    proxy = _proxy(tmp_path)
    assert (await proxy.post(route, headers={"X-Axor-Upload-Token": "user-jwt"})).status_code == 200
    assert (await proxy.post(route)).status_code == 200
    assert spawned == ["user-jwt", "proxy-own-key"]   # the caller's, else the proxy's


async def test_a_refused_upload_does_not_show_the_planes_address(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def refused(backend_url: str, ingest_key: str | None = None, **_: object) -> dict:
        req = httpx.Request("POST", "http://backend:8400/v1/ingest/gov_1")
        raise httpx.HTTPStatusError("401", request=req, response=httpx.Response(401, request=req))

    monkeypatch.setattr("axor_proxy.governed.spawn_governed_node", refused)
    r = await _proxy(tmp_path).post("/axor/governed/spawn")
    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "backend:8400" not in detail and "HTTP 401" in detail


async def test_a_refused_claim_is_a_failed_test_not_a_finished_one(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused claim came back as a 200 and the page called it a run that
    "did not upload". It is a test that did not finish, and says why."""
    async def refused(self: object, run_id: str, script: list, claim: dict) -> dict:
        return {"error": "unauthorized", "detail": "this proxy requires a bearer token"}

    monkeypatch.setattr("axor_proxy.agent.ScriptedAgent.run", refused)
    state = ProxyState(tools={"web_search": "http://upstream.test/s"}, trace_dir=tmp_path,
                       client=httpx.AsyncClient(transport=_upstream(), base_url="http://upstream.test"))
    app = create_app(state)
    page = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://proxy.test")
    state.agent_client = page
    run_id = (await page.post("/axor/runs", json={
        "scenario": "tool-deprivation", "faults": [{"tool": "web_search", "mode": "silent_fail"}],
    })).json()["run_id"]
    r = await page.post(f"/axor/runs/{run_id}/simulate", json={})
    assert r.status_code == 502
    assert r.json()["detail"] == "the test could not finish: this proxy requires a bearer token"
