"""The proxy's own governor — verdicts at the HTTP boundary (analysis point 2).

The proxy held no governor at all. Outside the demo node, `verdict` did not
appear anywhere in the package: `TraceRecorder` wrote `EventKind`s and
`RunManager.record` had no parameter for a verdict or a gate, so nothing it
produced could carry either. That is observation, not governance — and
`/v1/ingest` was correspondingly poorer than a trace from a wrapped agent.

The boundary is the one integration that does NOT reduce to `enforcement="off"`
on a `WrappedToolset`: there is no callable to wrap. The governor underneath it
is the same one, so it is held per run and consulted per call:

    mcp:web_search  tool_call   verdict=pass  ledger={"registered": true, "value_ref": "v1"}
    mcp:slack_post  tool_call   verdict=deny  gate=taint_floor

Two things are deliberately NOT inferred. Manifests are the operator's: an MCP
`tools/list` names a tool and describes it, and nothing in that says whether it
EXPORTS — `/v1/wrap/manifests` refuses an unclassified tool for the same reason.
And a call whose arguments the boundary cannot see is recorded as ungoverned
*with the reason*, never as one that passed.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from axor_proxy.app import ProxyState, create_app
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

WEB_OUT = "EXTERNAL: quarterly rates rose 4% (unverified web content)"

MANIFESTS: list[dict] = [
    {"schema_version": "tool-manifest/v1", "id": "mcp:web_search",
     "description": "search the web", "untrusted_fields": ["*"],
     "args_schema": {"type": "object"},
     "effect": {"default_class": "READ", "driving_args": ["q"]}},
    {"schema_version": "tool-manifest/v1", "id": "mcp:slack_post",
     "description": "post to slack", "args_schema": {"type": "object"},
     "effect": {"default_class": "EXPORT", "driving_args": ["text"]}},
]


def upstream() -> Starlette:
    async def handler(request: Request) -> JSONResponse:
        body = await request.json()
        name = body.get("params", {}).get("name")
        return JSONResponse({
            "jsonrpc": "2.0", "id": body.get("id"),
            "result": WEB_OUT if name == "web_search" else "posted",
        })

    return Starlette(routes=[Route("/{path:path}", handler, methods=["POST"])])


@pytest.fixture
async def proxy(tmp_path: Path) -> httpx.AsyncClient:
    up = httpx.AsyncClient(transport=httpx.ASGITransport(app=upstream()),
                           base_url="http://upstream.test")
    state = ProxyState(tools={"mcp": "http://upstream.test/rpc",
                              "plain": "http://upstream.test/plain"},
                       trace_dir=tmp_path, client=up)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(state)),
        base_url="http://proxy.test",
    ) as c:
        yield c
    await up.aclose()


async def arm(proxy: httpx.AsyncClient, **body: object) -> httpx.Response:
    return await proxy.post("/axor/runs", json={"scenario": "s", "faults": [], **body})


async def rpc(proxy: httpx.AsyncClient, tool: str, args: dict, n: int = 1) -> httpx.Response:
    return await proxy.post("/t/mcp/", json={
        "jsonrpc": "2.0", "id": n, "method": "tools/call",
        "params": {"name": tool, "arguments": args}})


def trace(tmp_path: Path, run_id: str) -> list[dict]:
    return [json.loads(x) for x in
            (tmp_path / f"{run_id}.jsonl").read_text().splitlines() if x.strip()]


class TestTheBoundaryProducesVerdicts:
    async def test_the_taint_edge_folds_across_two_calls(
        self, proxy: httpx.AsyncClient, tmp_path: Path,
    ) -> None:
        """The web read taints a value; the egress call carrying it is denied —
        at an HTTP boundary, from the operator's manifests, with no callable
        anywhere in the path."""
        run_id = (await arm(proxy, manifests=MANIFESTS)).json()["run_id"]
        await rpc(proxy, "web_search", {"q": "quarterly rates"}, 1)
        await rpc(proxy, "slack_post", {"text": WEB_OUT}, 2)

        calls = [e for e in trace(tmp_path, run_id) if e["kind"] == "tool_call"]
        assert [c["payload"]["tool"] for c in calls] == [
            "mcp:web_search", "mcp:slack_post"]
        assert [c["verdict"] for c in calls] == ["pass", "deny"]
        assert calls[1]["gate"] == "taint_floor"
        assert calls[1]["payload"]["category"] == "taint_enforcement"
        assert "tainted" in calls[1]["payload"]["reason"]

    async def test_the_read_is_registered_in_the_ledger(
        self, proxy: httpx.AsyncClient, tmp_path: Path,
    ) -> None:
        """A per-value gate is only as good as what the ledger holds, so a
        governed run reads a non-streaming answer instead of relaying it."""
        run_id = (await arm(proxy, manifests=MANIFESTS)).json()["run_id"]
        await rpc(proxy, "web_search", {"q": "rates"})
        result = next(e for e in trace(tmp_path, run_id) if e["kind"] == "tool_result")
        assert result["payload"]["ledger"]["registered"] is True
        assert result["payload"]["ledger"]["value_ref"]

    async def test_the_agent_is_never_blocked(
        self, proxy: httpx.AsyncClient,
    ) -> None:
        """Observe-only is the proxy's third rule; the verdict is recorded and
        the call proceeds — axor-wrap spells the same posture
        `enforcement="off"`."""
        await arm(proxy, manifests=MANIFESTS)
        denied = await rpc(proxy, "slack_post", {"text": WEB_OUT})
        assert denied.status_code == 200

    async def test_an_ungoverned_run_is_unchanged(
        self, proxy: httpx.AsyncClient, tmp_path: Path,
    ) -> None:
        """No manifests, no governor: the trace keeps exactly the shape it had,
        rather than gaining empty governance fields that mean nothing."""
        armed = await arm(proxy)
        assert armed.json()["governed"] is False
        run_id = armed.json()["run_id"]
        await rpc(proxy, "slack_post", {"text": WEB_OUT})
        for event in trace(tmp_path, run_id):
            assert event["verdict"] is None
            assert event["gate"] is None
            assert "governance" not in event["payload"]
            assert "ledger" not in event["payload"]


class TestWhatItRefusesToGuess:
    async def test_a_call_with_no_observable_arguments_says_so(
        self, proxy: httpx.AsyncClient, tmp_path: Path,
    ) -> None:
        """A plain HTTP tool names no arguments. Evaluating it against `{}`
        would record a `pass` that means nothing — the exact shape of a verdict
        that is not a verdict."""
        run_id = (await arm(proxy, manifests=MANIFESTS)).json()["run_id"]
        await proxy.get("/t/plain/")
        call = next(e for e in trace(tmp_path, run_id) if e["kind"] == "tool_call")
        assert call["verdict"] is None
        assert call["payload"]["governance"]["governed"] is False
        assert "arguments not observable" in call["payload"]["governance"]["reason"]

    async def test_arguments_never_reach_the_trace(
        self, proxy: httpx.AsyncClient, tmp_path: Path,
    ) -> None:
        """"No raw bodies persisted" is rule four, and an argument map IS the
        body. The governor sees them; the trace keeps the verdict."""
        run_id = (await arm(proxy, manifests=MANIFESTS)).json()["run_id"]
        await rpc(proxy, "web_search", {"q": "a-very-distinctive-secret-query"})
        assert "a-very-distinctive-secret-query" not in json.dumps(
            trace(tmp_path, run_id))

    async def test_manifests_that_do_not_compile_refuse_the_arm(
        self, proxy: httpx.AsyncClient,
    ) -> None:
        """A run armed with manifests the governor cannot be built from is a
        400 naming the reason — not a run that quietly records no verdicts."""
        r = await arm(proxy, manifests=[{"id": "x", "effect": "not-an-object"}])
        assert r.status_code == 400
        assert r.json()["error"] == "bad_manifests"

    async def test_manifests_of_the_wrong_type_refuse_the_arm(
        self, proxy: httpx.AsyncClient,
    ) -> None:
        r = await arm(proxy, manifests="web_search")
        assert r.status_code == 400
        assert "tool-manifest/v1" in r.json()["detail"]
