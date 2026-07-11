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


async def test_claim_uploads_trace_and_evidence_and_autopins(stack) -> None:  # noqa: ANN001
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

    # discrepancy-bearing trace auto-pinned must-block -> regression sees it
    report = (await backend.post("/v1/regression", json={
        "config": {"allowed_tools": ["web_search"], "egress_sinks": []},
    })).json()
    assert any(r["run_id"] == run_id and r["side"] == "must_block"
               for r in report["rows"])
