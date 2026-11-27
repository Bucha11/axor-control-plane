"""QA-192: every chaos mode the page offers finishes with a verdict.

"poisoning its results" and "hiding an instruction in its output" never
finished on the example agent: the scripted agent's answer did not take up the
planted string, so no case was derived, and only a case marked a run finished —
the page said "Run in progress" for good. Proxy and backend, in-process.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.chaos import MODES
from axor_proxy.app import ProxyState, create_app as create_proxy
from axor_proxy.upload import BackendUploader
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route


async def _doc(request: object) -> JSONResponse:
    return JSONResponse({"results": [{"content": "real doc"}]})


@pytest.mark.parametrize("mode", MODES)
async def test_qa192_every_offered_mode_finishes(tmp_path: pathlib.Path, mode: str) -> None:
    backend = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/c.db",
                         operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=backend),
                                 base_url="http://cp.test") as cp, \
            backend.router.lifespan_context(backend):
        upstream = httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(
            routes=[Route("/{p:path}", _doc, methods=["GET", "POST"])])), base_url="http://up.test")
        state = ProxyState(tools={"web_search": "http://up.test/s"}, trace_dir=tmp_path,
                           client=upstream,
                           uploader=BackendUploader("http://cp.test", client=cp))
        proxy = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_proxy(state)),
                                  base_url="http://proxy.test")
        state.self_base_url, state.agent_client = "http://proxy.test", proxy
        run_id = (await proxy.post("/axor/runs", json={
            "scenario": "tool-deprivation", "faults": [{"tool": "web_search", "mode": mode}],
        })).json()["run_id"]
        r = await proxy.post(f"/axor/runs/{run_id}/simulate", json={})
        assert r.status_code == 200 and r.json()["upload"]["uploaded"], r.text
        run = next(x for x in (await cp.get("/v1/runs")).json() if x["run_id"] == run_id)
        assert run["completed"] is True, (mode, run)
