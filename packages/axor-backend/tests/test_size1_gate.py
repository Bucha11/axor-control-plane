"""THE SIZE-1 REGRESSION GATE (spec v2, header invariant).

A tree of size 1 must produce the exact behavior the deployed v0.13 path
produces — byte-identical EvidenceCase, identical replay derivations. These
tests re-derive both from the production code paths and byte-compare against
the committed golden fixtures (generated once by scripts/gen_size1_golden.py).

DO NOT regenerate the fixtures to make this file pass. A diff here means the
multi-agent layer changed single-agent production behavior — that is a
production break, not a stale fixture. Regeneration is an explicit operator
decision recorded in the commit that does it.
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from axor_backend.demo import EX_BLOCK_EVENTS
from axor_backend.replay_api import parse_trace, scrubber_payload
from axor_core.kernel.replay import replay
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

GOLDEN = pathlib.Path(__file__).parent / "golden" / "size1"


def canonical_dump(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def test_size1_scrubber_bytes_identical() -> None:
    """The kernel fold over the canonical single-node adapter trace derives the
    exact scrubber payload production derives today."""
    events = parse_trace([json.dumps(e) for e in EX_BLOCK_EVENTS])
    got = canonical_dump(scrubber_payload(replay(events)))
    want = (GOLDEN / "scrubber.json").read_text("utf-8").rstrip("\n")
    assert got == want, (
        "size-1 replay derivation changed — this is a production break, "
        "not a fixture update"
    )


@pytest.fixture
async def proxy_client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    """The real proxy pipeline, in-process, with a fixed mock upstream — the
    same wiring the axor-proxy suite uses."""
    from axor_proxy.app import ProxyState, create_app

    async def handler(request: object) -> JSONResponse:
        return JSONResponse({"results": [{"content": "real doc"}]})

    upstream = httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=Starlette(routes=[Route("/{path:path}", handler,
                                        methods=["GET", "POST"])])
        ),
        base_url="http://upstream.test",
    )
    state = ProxyState(
        tools={"web_search": "http://upstream.test/s"},
        trace_dir=tmp_path,
        client=upstream,
    )
    app = create_app(state)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )
    state.self_base_url = "http://proxy.test"
    state.agent_client = client
    yield client
    await client.aclose()
    await upstream.aclose()


async def test_size1_evidence_bytes_identical(proxy_client: httpx.AsyncClient) -> None:
    """The canonical fabrication run (fault at the only node, claim, audit)
    produces the byte-identical EvidenceCase list."""
    run_id = (await proxy_client.post("/axor/runs", json={
        "scenario": "tool-deprivation",
        "faults": [{"tool": "web_search", "mode": "silent_fail"}],
    })).json()["run_id"]
    result = (await proxy_client.post(f"/axor/runs/{run_id}/simulate", json={})).json()

    got = canonical_dump(result["evidence"])
    want = (GOLDEN / "evidence.json").read_text("utf-8").rstrip("\n")
    assert got == want, (
        "size-1 EvidenceCase changed byte-for-byte — this is a production "
        "break, not a fixture update"
    )
