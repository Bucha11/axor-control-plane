"""Value provenance, derived per run (spec decision 6).

There is no provenance store any more, and the first class here is why: value
refs are minted per trace from a counter that restarts at zero, so a store keyed
on them merged unrelated values across runs.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.provenance import derivation_edges, khop
from axor_core.kernel.events import SCHEMA_VERSION


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={}, allow_unsigned=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        c._app = app  # type: ignore[attr-defined]
        yield c


class _E:
    """A kernel event, only the columns provenance reads."""

    def __init__(self, kind: str, node: str = "A", **payload: object) -> None:
        from axor_core.kernel.events import EventKind

        self.kind = EventKind(kind)
        self.node_id = node
        self.causal_root = None
        self.payload = dict(payload)


def _call(node: str, *refs: str) -> _E:
    return _E("tool_call", node, arg_refs={f"a{i}": r for i, r in enumerate(refs)})


def _result(node: str, ref: str) -> _E:
    return _E("tool_result", node, value_ref=ref)


# ── the collision that retired the store ─────────────────────────────────────

class TestARefIsOnlyMeaningfulInsideItsRun:
    """``axor_wrap.trace`` builds a fresh ``_Ledger`` per trace and its counter
    starts at zero, so the first external input of EVERY run is ``v_ext_1``.

    The retired GraphStore held those refs as global node ids with the run id on
    the EDGE: Monday's ``v_ext_1`` and Friday's ``v_ext_1`` were one node, and a
    caller asking for one was answered with the other's edges. Deriving from the
    run's own events cannot express that mistake — the run is the input.
    """

    MONDAY = [_call("A", "v_ext_1"), _result("A", "v_model_2")]
    FRIDAY = [_call("A", "v_ext_1"), _result("A", "v_other_2")]

    def test_two_runs_minting_the_same_ref_do_not_share_a_neighbourhood(self) -> None:
        monday = khop(self.MONDAY, "v_ext_1", k=3, limit=50)
        friday = khop(self.FRIDAY, "v_ext_1", k=3, limit=50)
        assert monday["nodes"] == ["v_ext_1", "v_model_2"]
        assert friday["nodes"] == ["v_ext_1", "v_other_2"]

    async def test_the_route_answers_about_the_run_in_its_path(
        self, client: httpx.AsyncClient
    ) -> None:
        for run_id, second in (("run_monday", "v_model_2"), ("run_friday", "v_other_2")):
            await client.post(f"/v1/ingest/{run_id}", json={"node_id": "A", "events": [
                {"schema_version": SCHEMA_VERSION, "seq": 0, "node_id": "A",
                 "kind": "tool_call", "ts": "t", "payload": {"arg_refs": {"a": "v_ext_1"}}},
                {"schema_version": SCHEMA_VERSION, "seq": 1, "node_id": "A",
                 "kind": "tool_result", "ts": "t", "payload": {"value_ref": second}},
            ]})
        monday = (await client.get(
            "/v1/runs/run_monday/provenance", params={"focus": "v_ext_1", "k": 3}
        )).json()
        assert monday["nodes"] == ["v_ext_1", "v_model_2"]
        assert "v_other_2" not in monday["nodes"]


# ── the derivation itself ────────────────────────────────────────────────────

def test_edges_and_khop_cut() -> None:
    events = [
        _call("A"), _result("A", "v_mail"),
        _call("A", "v_mail"), _result("A", "v_summary"),
        _call("A", "v_summary"), _result("A", "v_export"),
    ]
    one = khop(events, "v_mail", k=1, limit=50)
    assert set(one["nodes"]) == {"v_mail", "v_summary"}
    two = khop(events, "v_mail", k=2, limit=50)
    assert set(two["nodes"]) == {"v_mail", "v_summary", "v_export"}
    assert {"src": "v_mail", "dst": "v_summary"} in two["edges"]


def test_khop_limit_caps_nodes() -> None:
    events: list[_E] = []
    for i in range(10):
        events += [_call("A", "root"), _result("A", f"v{i}")]
    assert len(khop(events, "root", k=1, limit=3)["nodes"]) <= 3


def test_the_same_derivation_twice_is_one_edge() -> None:
    """An edge is a set member, not a count: a run that calls the same tool with
    the same inputs twice records the derivation twice."""
    events = [
        _call("A", "v_in"), _result("A", "v_out"),
        _call("A", "v_in"), _result("A", "v_out"),
    ]
    assert derivation_edges(events) == [{"src": "v_in", "dst": "v_out"}]


class TestDerivationsArePairedPerNode:
    """A run can carry several nodes (migration 0004), and a kernel event has no
    call id — only `seq`, `node_id` and the payload — so a call is paired with
    its result by node plus order.

    One pending list for the whole run did not merely miss edges when two nodes
    interleaved. It recorded a WRONG one: with A calling, B calling, A
    answering, B answering, it wrote `B's input -> A's output`, declaring one
    node's value the origin of another node's, in the index whose entire job is
    origin.
    """

    def test_interleaved_nodes_do_not_borrow_each_others_inputs(self) -> None:
        edges = derivation_edges([
            _call("A", "v_a"), _call("B", "v_b"),
            _result("A", "out_a"), _result("B", "out_b"),
        ])
        assert {(e["src"], e["dst"]) for e in edges} == {
            ("v_a", "out_a"), ("v_b", "out_b")}

    def test_a_single_node_trace_is_unchanged(self) -> None:
        assert len(derivation_edges([_call("A", "v1"), _result("A", "v2")])) == 1

    def test_a_result_with_no_call_of_its_own_derives_from_nothing(self) -> None:
        """Node B answering without having called claims no provenance, even
        while node A has a call outstanding."""
        assert derivation_edges([_call("A", "v_a"), _result("B", "out_b")]) == []

    def test_a_node_consumes_its_pending_inputs_once(self) -> None:
        """The second result on a node whose call was already answered derives
        from nothing — it is a different call, and its own arg_refs would have
        been recorded had it made one."""
        assert len(derivation_edges([
            _call("A", "v_a"), _result("A", "out1"), _result("A", "out2"),
        ])) == 1


# ── the HTTP surface ─────────────────────────────────────────────────────────

class TestArgumentsAreBoundedBeforeTheWalk:
    """`k` and `limit` went from the query string into the walk untouched, so a
    caller sized the work. They are declared bounds now: too much is a 422
    naming the ceiling, never a 500 and never a walk of the whole run."""

    async def test_out_of_range_arguments_are_refused_with_a_reason(
        self, client: httpx.AsyncClient
    ) -> None:
        from axor_backend.limits import MAX_KHOP_K, MAX_KHOP_LIMIT

        for query in (f"k={MAX_KHOP_K + 1}", "k=0", "k=-1",
                      f"limit={MAX_KHOP_LIMIT + 1}", "limit=0", "limit=-5"):
            r = await client.get(f"/v1/runs/r/provenance?focus=v1&{query}")
            assert r.status_code == 422, (query, r.status_code)

    async def test_the_bounds_themselves_reach_the_run(
        self, client: httpx.AsyncClient
    ) -> None:
        from axor_backend.limits import MAX_KHOP_K, MAX_KHOP_LIMIT

        await client.post("/v1/demo/seed-adapter-runs")
        r = await client.get(
            f"/v1/runs/ex_block/provenance?focus=v_mail"
            f"&k={MAX_KHOP_K}&limit={MAX_KHOP_LIMIT}")
        assert r.status_code == 200


async def test_an_unknown_run_is_a_404_not_an_empty_graph(
    client: httpx.AsyncClient
) -> None:
    """The retired store answered "a graph of one" for a ref it had never seen,
    which is also what it answered for a run that does not exist."""
    r = await client.get("/v1/runs/nope/provenance", params={"focus": "v1"})
    assert r.status_code == 404


async def test_seed_adapter_runs_lights_up_deep_surfaces(
    client: httpx.AsyncClient,
) -> None:
    """The seed makes counterfactual divergence, provenance, and two-sided
    regression all demonstrable in-app with real adapter-fidelity data."""
    r = await client.post("/v1/demo/seed-adapter-runs")
    body = r.json()
    assert set(body["seeded"]) == {"ex_block", "ex_pass"}
    cfg = body["config"]

    # 1. Golden replay reproduces the recorded taint deny (0 divergence).
    rep = (await client.post("/v1/replay/ex_block", json={"config": cfg})).json()
    assert rep["first_divergence"] is None
    slack = next(s for s in rep["steps"] if s["payload"].get("tool") == "slack_post")
    assert slack["reevaluated_verdict"] == "deny"

    # 2. Counterfactual (drop bash from the capability table) → divergence.
    noexec = dict(cfg, allowed_tools=[t for t in cfg["allowed_tools"] if t != "bash"])
    cf = (await client.post("/v1/replay/ex_block", json={"config": noexec})).json()
    assert cf["first_divergence"] == 2

    # 3. Provenance derives the edge from the stored events.
    g = (await client.get("/v1/runs/ex_block/provenance",
                          params={"focus": "v_mail", "k": 3})).json()
    assert {"src": "v_mail", "dst": "v_sum"} in g["edges"]

    # 4. Two-sided regression: block held + pass passes → safe.
    reg = (await client.post("/v1/regression", json={"config": cfg})).json()
    sides = {row["run_id"]: (row["side"], row["result"]) for row in reg["rows"]}
    assert sides["ex_block"] == ("must_block", "held")
    assert sides["ex_pass"] == ("must_pass", "passed")
    assert reg["safe_to_ship"] is True

    # A config that breaks the legit flow → the must_pass side regresses (teeth).
    broken = dict(cfg, allowed_tools=[t for t in cfg["allowed_tools"]
                                      if t != "notes_write"])
    reg2 = (await client.post("/v1/regression", json={"config": broken})).json()
    assert reg2["safe_to_ship"] is False
