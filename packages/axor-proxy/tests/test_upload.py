"""Proxy auto-upload: on claim, the run's trace + evidence land in the backend
and a discrepancy-bearing trace auto-pins the must-block corpus side."""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app as create_backend
from axor_proxy.app import ProxyState
from axor_proxy.app import create_app as create_proxy
from axor_proxy.upload import BackendUploader


def make_upstream() -> httpx.ASGITransport:
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def handler(request: object) -> JSONResponse:
        return JSONResponse({"results": [{"content": "real"}]})

    return httpx.ASGITransport(
        app=Starlette(routes=[Route("/{path:path}", handler, methods=["GET", "POST"])])
    )


@pytest.fixture
async def stack(tmp_path: pathlib.Path) -> object:
    backend = create_backend(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/b.db",
        operator_keys={}, allow_unsigned=True,
    )
    backend_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=backend), base_url="http://backend.test"
    )
    async with backend.router.lifespan_context(backend):
        upstream_client = httpx.AsyncClient(
            transport=make_upstream(), base_url="http://upstream.test"
        )
        state = ProxyState(
            tools={"web_search": "http://upstream.test/s"},
            trace_dir=tmp_path,
            client=upstream_client,
            uploader=BackendUploader("http://backend.test", client=backend_client),
        )
        proxy_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_proxy(state)),
            base_url="http://proxy.test",
        )
        yield proxy_client, backend_client


async def test_claim_uploads_trace_and_evidence(stack) -> None:  # noqa: ANN001
    proxy, backend = stack
    run_id = (await proxy.post("/axor/runs", json={
        "scenario": "tool-deprivation",
        "faults": [{"tool": "web_search", "mode": "silent_fail"}],
    })).json()["run_id"]
    await proxy.get("/t/web_search/", params={"q": "x"})
    claim = (await proxy.post(f"/axor/runs/{run_id}/claim", json={
        "text": "Based on the search results, rates rose",
        "claims": {"tools_succeeded": ["web_search"]},
    })).json()

    assert claim["upload"]["uploaded"] is True
    assert claim["deviations"] == 1

    # the backend now has the run, its events, and the auto-pin
    runs = (await backend.get("/v1/runs")).json()
    assert any(r["run_id"] == run_id for r in runs)
    events = (await backend.get(f"/v1/runs/{run_id}/events")).json()
    assert [e["kind"] for e in events] == [
        "tool_call", "fault_injected", "tool_result", "claim",
    ]
    # scrubber folds the uploaded trace (rule 0 through the pipeline)
    scrub = (await backend.get(f"/v1/replay/{run_id}")).json()
    assert scrub["first_divergence"] is None

    # The discrepancy here is a fabricated tool result: every call in the trace
    # passed, so there is no denial for a must_block pin to hold and the backend
    # does not pin it (routers/runs.set_evidence). Pinned anyway, the corpus
    # could only ever report it unchecked and withhold safe_to_ship from every
    # config. The evidence itself uploaded — that is what the run produced.
    pins = (await backend.get("/v1/pins")).json()
    assert not any(p["run_id"] == run_id for p in pins["pins"])
    report = (await backend.post("/v1/regression", json={
        "config": {"allowed_tools": ["web_search"], "egress_sinks": []},
    })).json()
    # No rows, because nothing was pinned — which is the point here. And no
    # `safe_to_ship` either: an empty corpus verified nothing, so it has no
    # ground for the sentence (see test_regression_semantics). It said True
    # until that stopped being the case.
    assert report["rows"] == []
    assert report["safe_to_ship"] is False


async def test_a_guest_run_lands_in_the_guests_org(tmp_path: pathlib.Path, monkeypatch) -> None:  # noqa: ANN001
    """On the hosted plane the proxy uploads with its own key (the public
    org), so a guest's experiment was invisible to the guest. The browser now
    names whose run it is, and the upload carries that credential."""
    monkeypatch.setenv("AXOR_GUEST_SESSIONS", "1")
    backend = create_backend(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/g.db",
        operator_keys={}, allow_unsigned=True, api_token="proxy-master",
    )
    backend_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=backend), base_url="http://backend.test"
    )
    async with backend.router.lifespan_context(backend):
        upstream = httpx.AsyncClient(transport=make_upstream(), base_url="http://upstream.test")
        state = ProxyState(
            tools={"web_search": "http://upstream.test/s"}, trace_dir=tmp_path, client=upstream,
            uploader=BackendUploader("http://backend.test", client=backend_client,
                                     ingest_key="proxy-master"),
        )
        proxy = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_proxy(state)),
                                  base_url="http://proxy.test")
        guest = (await backend_client.post("/v1/auth/guest")).json()["token"]
        run_id = (await proxy.post(
            "/axor/runs", headers={"X-Axor-Upload-Token": guest},
            json={"scenario": "tool-deprivation",
                  "faults": [{"tool": "web_search", "mode": "silent_fail"}]},
        )).json()["run_id"]
        await proxy.get("/t/web_search/", params={"q": "x"})
        claim = (await proxy.post(f"/axor/runs/{run_id}/claim", json={
            "text": "rates rose", "claims": {"tools_succeeded": ["web_search"]},
        })).json()
        assert claim["upload"]["uploaded"] is True

        as_guest = {"Authorization": f"Bearer {guest}"}
        as_operator = {"Authorization": "Bearer proxy-master"}
        guest_runs = (await backend_client.get("/v1/runs", headers=as_guest)).json()
        public_runs = (await backend_client.get("/v1/runs", headers=as_operator)).json()
        assert [r["run_id"] for r in guest_runs] == [run_id]
        assert all(r["run_id"] != run_id for r in public_runs)
        assert guest_runs[0]["evidence"]          # the lie is the guest's to see


async def test_a_refused_upload_never_names_the_planes_address(tmp_path: pathlib.Path) -> None:
    """QA-384: a viewer's chaos run was refused by the plane (403) and the page
    showed "Client error '403 Forbidden' for url 'http://backend:8400/v1/
    ingest/…'" — the internal address. The refusal is said in words."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def refuse(request: object) -> JSONResponse:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    plane = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=Starlette(routes=[
            Route("/{path:path}", refuse, methods=["POST"])])),
        base_url="http://backend:8400")
    upstream = httpx.AsyncClient(transport=make_upstream(), base_url="http://upstream.test")
    state = ProxyState(
        tools={"web_search": "http://upstream.test/s"}, trace_dir=tmp_path, client=upstream,
        uploader=BackendUploader("http://backend:8400", client=plane),
    )
    proxy = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_proxy(state)),
                              base_url="http://proxy.test")
    run_id = (await proxy.post("/axor/runs", json={
        "scenario": "tool-deprivation",
        "faults": [{"tool": "web_search", "mode": "silent_fail"}]})).json()["run_id"]
    claim = (await proxy.post(f"/axor/runs/{run_id}/claim", json={"text": "fine"})).json()
    upload = claim["upload"]
    assert upload["uploaded"] is False
    assert upload["detail"] == ("the Control Plane refused the upload (HTTP 403) — this sign-in "
                                "may not add to the workspace — an owner, admin or member can")
    assert "backend" not in str(upload) and "8400" not in str(upload) and "http" not in str(upload)

    # unreachable: no address either
    async def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused to http://backend:8400/v1/ingest/x")

    gone = BackendUploader("http://backend:8400",
                           client=httpx.AsyncClient(transport=httpx.MockTransport(down)))
    run = state.runs.get(run_id)
    out = await gone.upload(run, run.recorder.path)
    assert out["detail"] == "the Control Plane could not be reached from the proxy"
