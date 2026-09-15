"""The read side: a typed door, a bounded ablation, and a loop that keeps serving.

Three things `analysis.py` did not do, all of them consequences of "every route
here is a pure kernel computation".

**The door was typed in one field out of three.** `config` was checked and
answered 400; the two fields that name the anchor went to the kernel as they
arrived, so `int("abc")`, `int({})` and an unhashable node id each left the
route as `500 {"error": "internal"}`.

**Ablation is not linear.** It replays the anchor's whole local sequence once
per upstream ref, and each replay is itself superlinear in what it folds:

       n reads   events        ms     growth
           100      201     245.7
           200      401    1203.5       x4.9
           400      801    6931.2       x5.8
           800     1601   41666.1       x6.0

41.7 s on one request, under `read` — the cheapest scope there is — and
`MAX_EVENTS_PER_RUN` is 250 000. A linear ceiling does not bound a
superquadratic route, so this one bounds itself and says how to narrow.

**It ran on the event loop.** Measured as the overshoot of 50 ms timers that
could not fire while one request folded a 401-event run:

    on the loop:   request 1408 ms, worst timer overshoot 904 ms
    off the loop:  request  938 ms, worst timer overshoot  31 ms

The backend is single-instance by design (docs/ops-limits.md), and
docker-compose polls `/v1/healthz` with `timeout: 5s` while two services gate
their start on it — so "the loop is busy" is "the deployment is unhealthy".
"""
from __future__ import annotations

import asyncio
import pathlib
import time

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.limits import MAX_ABLATION_REFS
from axor_core.kernel.events import SCHEMA_VERSION

TOKEN = "t"
H = {"Authorization": f"Bearer {TOKEN}"}


def _ev(seq: int, kind: str, verdict: str | None = None,
        gate: str | None = None, **payload: object) -> dict:
    return {"schema_version": SCHEMA_VERSION, "seq": seq, "node_id": "n1",
            "kind": kind, "ts": f"seq:{seq}", "causal_root": None,
            "gate": gate, "verdict": verdict, "payload": payload}


def chain(n: int) -> tuple[list[dict], int]:
    """n web reads feeding each other, then a denied egress carrying the last."""
    out: list[dict] = []
    seq = 0
    for i in range(n):
        out.append(_ev(seq, "tool_call", "pass", tool="read", args={"i": i},
                       arg_refs=({"x": f"v{i - 1}"} if i else {})))
        seq += 1
        out.append(_ev(seq, "tool_result", tool="read", value_ref=f"v{i}",
                       root={"sources": ["web"], "sensitive": False}))
        seq += 1
    out.append(_ev(seq, "tool_call", "deny", "taint_floor", tool="post",
                   args={"text": "x"}, arg_refs={"text": f"v{n - 1}"},
                   reason="taint", category="taint_enforcement"))
    return out, seq


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/a.db",
        operator_keys={}, allow_unsigned=True, api_token=TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test",
        timeout=300,
    ) as c, app.router.lifespan_context(app):
        yield c


async def seed(client: httpx.AsyncClient, run_id: str, n: int) -> int:
    lines, anchor = chain(n)
    r = await client.post(f"/v1/ingest/{run_id}", json={"events": lines}, headers=H)
    assert r.status_code == 202, r.text
    return anchor


class TestTheDoorIsTypedInEveryField:
    @pytest.mark.parametrize(("body", "detail"), [
        ({"anchor_node": "n1", "anchor_seq": "abc"}, "anchor_seq must be an integer"),
        ({"anchor_node": "n1", "anchor_seq": {}}, "anchor_seq must be an integer"),
        ({"anchor_node": "n1", "anchor_seq": 1.5}, "anchor_seq must be an integer"),
        ({"anchor_node": "n1", "anchor_seq": True}, "anchor_seq must be an integer"),
        ({"anchor_node": {}, "anchor_seq": 4}, "anchor_node must be a string"),
        ({"anchor_node": ["n1"], "anchor_seq": 4}, "anchor_node must be a string"),
    ])
    async def test_a_bad_anchor_is_the_callers_fault(
        self, client: httpx.AsyncClient, body: dict, detail: str,
    ) -> None:
        await seed(client, "r", 3)
        r = await client.post("/v1/runs/r/influence", json=body, headers=H)
        assert r.status_code == 400, r.text
        assert r.json()["detail"] == detail

    async def test_config_is_still_checked(self, client: httpx.AsyncClient) -> None:
        """The one field that WAS defended stays defended."""
        anchor = await seed(client, "r", 3)
        r = await client.post("/v1/runs/r/influence", headers=H, json={
            "anchor_node": "n1", "anchor_seq": anchor, "config": "egress"})
        assert r.status_code == 400
        assert "config must be an object" in r.text

    async def test_an_anchor_that_names_no_event_is_a_404(
        self, client: httpx.AsyncClient,
    ) -> None:
        await seed(client, "r", 3)
        r = await client.post("/v1/runs/r/influence", headers=H, json={
            "anchor_node": "n1", "anchor_seq": 9999})
        assert r.status_code == 404


class TestAblationBoundsItself:
    async def test_too_many_upstream_values_is_refused_not_truncated(
        self, client: httpx.AsyncClient,
    ) -> None:
        """Ranking the first N would answer a different question under the name
        of the one that was asked, and the caller could not see the swap."""
        n = MAX_ABLATION_REFS + 1
        anchor = await seed(client, "big", n)
        r = await client.post("/v1/runs/big/influence", headers=H, json={
            "anchor_node": "n1", "anchor_seq": anchor})
        assert r.status_code == 422
        assert f"{n} upstream values" in r.json()["detail"]
        assert f"at most {MAX_ABLATION_REFS}" in r.json()["detail"]

    async def test_the_refusal_is_fast(self, client: httpx.AsyncClient) -> None:
        """The point of a bound is that the refusal costs nothing. The same case
        ran for 41.7 s before answering."""
        anchor = await seed(client, "big", MAX_ABLATION_REFS + 1)
        started = time.perf_counter()
        r = await client.post("/v1/runs/big/influence", headers=H, json={
            "anchor_node": "n1", "anchor_seq": anchor})
        assert r.status_code == 422
        assert time.perf_counter() - started < 5.0

    async def test_naming_the_refs_answers_the_same_case(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The escape hatch: a case past the bound is still answerable, for the
        values the caller actually wants ranked."""
        n = MAX_ABLATION_REFS + 1
        anchor = await seed(client, "big", n)
        r = await client.post("/v1/runs/big/influence", headers=H, json={
            "anchor_node": "n1", "anchor_seq": anchor,
            "refs": [f"v{i}" for i in (n - 1, n - 2, n - 3)]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ablated_refs"] == 3
        assert body["available_refs"] == n
        assert {e["ref"] for e in body["ranking"]} == {
            f"v{n - 1}", f"v{n - 2}", f"v{n - 3}"}

    async def test_a_ref_outside_the_case_is_refused(
        self, client: httpx.AsyncClient,
    ) -> None:
        """Silently dropping it would rank fewer values than the caller asked
        for and report a number they did not choose."""
        anchor = await seed(client, "r", 3)
        r = await client.post("/v1/runs/r/influence", headers=H, json={
            "anchor_node": "n1", "anchor_seq": anchor, "refs": ["v0", "v9999"]})
        assert r.status_code == 400
        assert "v9999" in r.json()["detail"]

    async def test_refs_of_the_wrong_shape_are_refused(
        self, client: httpx.AsyncClient,
    ) -> None:
        anchor = await seed(client, "r", 3)
        r = await client.post("/v1/runs/r/influence", headers=H, json={
            "anchor_node": "n1", "anchor_seq": anchor, "refs": "v0"})
        assert r.status_code == 400
        assert "refs must be a list" in r.json()["detail"]

    async def test_a_case_within_the_bound_still_ranks_everything(
        self, client: httpx.AsyncClient,
    ) -> None:
        anchor = await seed(client, "small", 5)
        body = (await client.post("/v1/runs/small/influence", headers=H, json={
            "anchor_node": "n1", "anchor_seq": anchor})).json()
        assert body["ablated_refs"] == body["available_refs"] == 5
        assert len(body["ranking"]) == 5


class TestTheAnchorLookupCannotRaiseOutOfACoroutine:
    """`next(e for e in events if ...)` with no default raised StopIteration,
    which Python re-raises out of a coroutine as a bare RuntimeError — a 500.

    The route reaches it only after the subgraph walk has already accepted the
    anchor, so it is unreachable from outside; that is a reason to test it
    directly, not a reason to leave an unexercised raise in the path.
    """

    def test_a_missing_anchor_is_a_404_not_a_stopiteration(self) -> None:
        from axor_backend.routers.analysis import _anchor_event
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _anchor_event([], "n1", 4)
        assert exc.value.status_code == 404
        assert "no event seq=4 at node 'n1'" in exc.value.detail

    def test_it_finds_the_anchor_it_is_given(self) -> None:
        from axor_backend.routers.analysis import _anchor_event
        from axor_core.kernel.events import Event, EventKind

        events = [
            Event(seq=s, node_id=n, kind=EventKind.TOOL_CALL, ts=f"seq:{s}",
                  payload={"tool": f"t{s}"})
            for n, s in (("n1", 0), ("n2", 4), ("n1", 4))
        ]
        assert _anchor_event(events, "n1", 4).payload["tool"] == "t4"


class TestTheKernelWorkDoesNotHoldTheLoop:
    async def test_timers_still_fire_while_a_fold_runs(
        self, client: httpx.AsyncClient,
    ) -> None:
        """A 50 ms timer that cannot fire for most of a second is a process
        that is answering nobody — including the liveness probe two compose
        services gate their start on."""
        anchor = await seed(client, "big", MAX_ABLATION_REFS)

        async def timers() -> float:
            worst = 0.0
            for _ in range(20):
                started = time.perf_counter()
                await asyncio.sleep(0.02)
                worst = max(worst, (time.perf_counter() - started) - 0.02)
            return worst

        overshoot, response = await asyncio.gather(
            timers(),
            client.post("/v1/runs/big/influence", headers=H, json={
                "anchor_node": "n1", "anchor_seq": anchor}),
        )
        assert response.status_code == 200
        # On the loop this was 904 ms for a smaller case; the threshold is a
        # wide multiple of the 31 ms measured off it, so the test fails on the
        # regression and not on a slow machine.
        assert overshoot < 0.4, f"the loop was starved for {overshoot * 1000:.0f} ms"

    @pytest.mark.parametrize(("method", "path", "body", "expected"), [
        # `_kernel_trace` is on every row on purpose: the first pass of this fix
        # handed off the folds and left the trace parse on the loop, and a
        # 60 000-event replay still starved it for 1141 ms.
        ("GET", "/v1/replay/r", None, {"_kernel_trace", "_replayed"}),
        ("POST", "/v1/replay/r", {"config": {}}, {"_kernel_trace", "_replayed"}),
        ("GET", "/v1/runs/r/subgraph?anchor_node=n1&anchor_seq=6", None,
         {"_kernel_trace", "_subgraph"}),
        ("GET", "/v1/runs/r/containment?anchor_node=n1&anchor_seq=6", None,
         {"_kernel_trace", "_subgraph", "containment_report"}),
        ("POST", "/v1/runs/r/influence", {"anchor_node": "n1", "anchor_seq": 6},
         {"_kernel_trace", "_subgraph", "influence_ranking"}),
    ])
    async def test_every_route_hands_its_kernel_work_off(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
        method: str, path: str, body: dict | None, expected: set[str],
    ) -> None:
        """Asserted at the seam, not by timing.

        `replay` is linear, so a run small enough for a fast test never starves
        the loop long enough to measure — a timing assertion on it would pass
        whether the work is handed off or not, which is a test that tests
        nothing. What must hold is that no route here calls the kernel inline.
        """
        from axor_backend import offload

        handed: list[str] = []
        real = offload.asyncio.to_thread

        async def counting(  # noqa: ANN202
            fn, /, *args: object, **kwargs: object,  # noqa: ANN001
        ):
            handed.append(getattr(fn, "__name__", repr(fn)))
            return await real(fn, *args, **kwargs)

        monkeypatch.setattr(offload.asyncio, "to_thread", counting)
        await seed(client, "r", 3)
        r = await client.request(method, path, headers=H, json=body)
        assert r.status_code == 200, r.text
        missing = expected - set(handed)
        assert not missing, (
            f"{method} {path} called {sorted(missing)} on the event loop "
            f"(handed off: {sorted(set(handed))})"
        )
