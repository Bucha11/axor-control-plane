"""Seeding canned runs into a tenant's own append-only event log.

Two things "seeding real stored events" implies, and a third about what the
evidence then means.

**A seed must not land in somebody else's run.** The run ids are fixed and
`ingest_events` is append-only with de-duplication on (node_id, seq). The demo
nodes are named `tree-*`, so nothing collided and everything was appended — a
tenant with a real run called `ex_tree` got eighteen fabricated governance
events added to it and replayed as one trace:

    events 4 -> 22; node ids now ['prod-node', 'tree-orch', 'tree-research',
                                  'tree-scraper', 'tree-writer']
    GET /v1/replay/ex_tree -> 200, 22 steps over a merged trace

**Re-seeding must re-seed.** "Idempotent by construction: re-seeding overwrites
the same run ids" was the module's claim; de-duplication is idempotent APPEND,
so a demo event changed in a later release at the same coordinate was silently
ignored and the deployment kept the old trace forever.

**And canned evidence is evidence about the demo.** Earlier on this branch an
empty corpus was made to withhold `safe_to_ship`, because "no evidence" is not
"the evidence is good". Two clicks on the in-app demo walked back through the
same hole by a different door:

    empty corpus:      safe_to_ship=False rows=0
    after a demo seed: safe_to_ship=True  rows=2 (both 'adapter-demo')
"""
from __future__ import annotations

import copy
import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.demo import DEMO_RUN_IDS, DEMO_SCENARIOS
from axor_core.kernel.events import SCHEMA_VERSION

TOKEN = "t"
H = {"Authorization": f"Bearer {TOKEN}"}


def _ev(seq: int, tool: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "seq": seq, "node_id": "prod-node",
            "kind": "tool_call", "ts": f"seq:{seq}", "causal_root": None,
            "gate": None, "verdict": "pass", "payload": {"tool": tool}}


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/d.db",
        operator_keys={}, allow_unsigned=True, api_token=TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test",
        timeout=120,
    ) as c, app.router.lifespan_context(app):
        yield c


class TestASeedDoesNotLandInSomebodyElsesRun:
    @pytest.mark.parametrize(("run_id", "route"), [
        ("ex_tree", "/v1/demo/seed-tree-run"),
        ("ex_block", "/v1/demo/seed-adapter-runs"),
        ("ex_pass", "/v1/demo/seed-adapter-runs"),
    ])
    async def test_a_real_run_under_a_demo_id_is_refused(
        self, client: httpx.AsyncClient, run_id: str, route: str,
    ) -> None:
        mine = [_ev(i, f"real_tool_{i}") for i in range(4)]
        await client.post(f"/v1/ingest/{run_id}", json={"events": mine}, headers=H)

        r = await client.post(route, headers=H)
        assert r.status_code == 409, r.text
        assert "is not a demo run" in r.json()["detail"]

        kept = (await client.get(f"/v1/runs/{run_id}/events", headers=H)).json()
        assert [e["payload"]["tool"] for e in kept] == [
            f"real_tool_{i}" for i in range(4)]
        assert {e["node_id"] for e in kept} == {"prod-node"}

    async def test_seeding_a_clean_tenant_works(
        self, client: httpx.AsyncClient,
    ) -> None:
        assert (await client.post(
            "/v1/demo/seed-tree-run", headers=H)).status_code == 200
        events = (await client.get("/v1/runs/ex_tree/events", headers=H)).json()
        assert events
        assert "prod-node" not in {e["node_id"] for e in events}

    async def test_the_run_row_says_it_is_a_demo(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The marker the refusal reads. It used to keep whatever row already
        existed, so the metadata said one thing and the events another."""
        await client.post("/v1/demo/seed-tree-run", headers=H)
        runs = {r["run_id"]: r for r in
                (await client.get("/v1/runs", headers=H)).json()}
        assert runs["ex_tree"]["scenario"] in DEMO_SCENARIOS
        assert set(runs) <= DEMO_RUN_IDS


class TestReSeedingReplaces:
    async def test_a_changed_demo_event_takes_effect(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from axor_backend import demo

        await client.post("/v1/demo/seed-tree-run", headers=H)
        first = (await client.get("/v1/runs/ex_tree/events", headers=H)).json()
        probe = next(e for e in first if e["payload"].get("tool"))

        changed = copy.deepcopy(demo.TREE_EVENTS)
        for event in changed:
            if (event["node_id"], event["seq"]) == (probe["node_id"], probe["seq"]):
                event["payload"]["tool"] = "RENAMED_IN_A_LATER_RELEASE"
        monkeypatch.setattr(demo, "TREE_EVENTS", changed)

        await client.post("/v1/demo/seed-tree-run", headers=H)
        second = (await client.get("/v1/runs/ex_tree/events", headers=H)).json()
        now = next(e for e in second
                   if (e["node_id"], e["seq"]) == (probe["node_id"], probe["seq"]))
        assert now["payload"]["tool"] == "RENAMED_IN_A_LATER_RELEASE"
        assert len(second) == len(first)

    async def test_a_removed_demo_event_is_gone(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Append could only ever add. A trace that lost an event in a later
        release kept it, which is the same defect read from the other side."""
        from axor_backend import demo

        await client.post("/v1/demo/seed-tree-run", headers=H)
        before = len((await client.get(
            "/v1/runs/ex_tree/events", headers=H)).json())
        monkeypatch.setattr(demo, "TREE_EVENTS", demo.TREE_EVENTS[:-1])
        await client.post("/v1/demo/seed-tree-run", headers=H)
        after = (await client.get("/v1/runs/ex_tree/events", headers=H)).json()
        assert len(after) == before - 1

    async def test_re_seeding_unchanged_is_still_stable(
        self, client: httpx.AsyncClient,
    ) -> None:
        # the same events; only their stamp moves, to the time of seeding
        def unstamped(evs: list[dict]) -> list[dict]:
            return [{k: v for k, v in e.items() if k != "ts"} for e in evs]

        await client.post("/v1/demo/seed-tree-run", headers=H)
        first = (await client.get("/v1/runs/ex_tree/events", headers=H)).json()
        await client.post("/v1/demo/seed-tree-run", headers=H)
        assert unstamped((await client.get(
            "/v1/runs/ex_tree/events", headers=H)).json()) == unstamped(first)


class TestCannedEvidenceIsNotEvidenceAboutThisDeployment:
    async def test_a_demo_seed_does_not_turn_the_gate_green(
        self, client: httpx.AsyncClient,
    ) -> None:
        empty = (await client.post("/v1/regression", json={}, headers=H)).json()
        assert empty["safe_to_ship"] is False

        await client.post("/v1/demo/seed-adapter-runs", headers=H)
        seeded = (await client.post("/v1/regression", json={}, headers=H)).json()
        assert seeded["safe_to_ship"] is False
        assert seeded["rows"] == []

    async def test_the_example_check_says_its_rows_are_canned(
        self, client: httpx.AsyncClient,
    ) -> None:
        seeded = (await client.post("/v1/demo/seed-adapter-runs", headers=H)).json()
        report = (await client.post("/v1/regression", json={
            "config": seeded["config"], "cases": "example"}, headers=H)).json()
        assert {r["run_id"]: (r["demo"], r["result"]) for r in report["rows"]} == {
            "ex_block": (True, "held"), "ex_pass": (True, "passed")}
        assert report["safe_to_ship"] is False

    async def test_one_pin_of_your_own_is_what_the_sentence_needs(
        self, client: httpx.AsyncClient,
    ) -> None:
        from axor_backend import demo

        await client.post("/v1/demo/seed-adapter-runs", headers=H)
        await client.post("/v1/ingest/mine",
                          json={"events": demo.EX_PASS_EVENTS}, headers=H)
        await client.post("/v1/pins/mine", json={"side": "must_pass"}, headers=H)
        report = (await client.post("/v1/regression", json={}, headers=H)).json()
        assert report["own_rows"] == 1
        assert report["demo_rows"] == 0
        assert report["safe_to_ship"] is True

    async def test_the_examples_cases_never_join_the_workspaces_pins(
        self, client: httpx.AsyncClient,
    ) -> None:
        """QA-400 / QA-386: seeding the example pinned its cases among the
        workspace's; the workspace's own rule then failed Safe to ship? on the
        example's normal flow. They are checked only against the example's
        rules, and a pin an older build left there is taken out."""
        from axor_backend import demo

        await client.post("/v1/demo/seed-adapter-runs", headers=H)
        pins = (await client.get("/v1/pins", headers=H)).json()
        assert pins["total"] == 0
        refused = await client.post("/v1/pins/ex_pass", json={"side": "must_pass"}, headers=H)
        assert refused.status_code == 400
        assert "the example's run" in refused.json()["detail"]

        # the workspace's own case, and its own rule: a tool the example's
        # normal flow needs is not in it
        mine = [
            {"schema_version": SCHEMA_VERSION, "seq": 0, "node_id": "mine",
             "kind": "tool_call", "ts": "seq:0", "causal_root": None,
             "gate": None, "verdict": "pass",
             "payload": {"tool": "summarize", "args": {"text": "x"}}},
        ]
        await client.post("/v1/ingest/mine", json={"events": mine}, headers=H)
        await client.post("/v1/pins/mine", json={"side": "must_pass"}, headers=H)
        own_rule = {"allowed_tools": ["summarize"], "egress_sinks": []}
        report = (await client.post(
            "/v1/regression", json={"config": own_rule}, headers=H)).json()
        assert [r["run_id"] for r in report["rows"]] == ["mine"]
        assert report["regressed"] == 0
        assert report["safe_to_ship"] is True

        # a pin an older build seeded is not the workspace's either
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await store.pin("ex_pass", "must_pass", "adapter-demo")
        assert {p["run_id"] for p in (await client.get("/v1/pins", headers=H)).json()["pins"]} == {"mine"}
        again = (await client.post(
            "/v1/regression", json={"config": own_rule}, headers=H)).json()
        assert [r["run_id"] for r in again["rows"]] == ["mine"]
        await client.post("/v1/demo/seed-adapter-runs", headers=H)
        assert {p["run_id"] for p in await store.pinned()} == {"mine"}
        assert demo.EX_PASS_EVENTS  # the example itself is still there to replay

    async def test_the_own_pin_alone_is_what_carries_the_sentence(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The mirror of the rule: demo rows neither grant the verdict nor are
        required for it."""
        from axor_backend import demo

        await client.post("/v1/ingest/mine",
                          json={"events": demo.EX_PASS_EVENTS}, headers=H)
        await client.post("/v1/pins/mine", json={"side": "must_pass"}, headers=H)
        report = (await client.post("/v1/regression", json={}, headers=H)).json()
        assert (report["own_rows"], report["demo_rows"]) == (1, 0)
        assert report["safe_to_ship"] is True
