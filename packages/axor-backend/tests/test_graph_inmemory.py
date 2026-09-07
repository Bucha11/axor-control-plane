"""InMemoryGraphStore + the /v1/graph HTTP surface (spec decision 6).

The in-memory store is the dev/test default (no kuzu wheel needed) and folds
ingested traces' arg_refs → value_ref derivations, so the taint graph is real
data. These mirror test_graph.py's Kuzu cases so both backends behave the same.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.graph import InMemoryGraphStore, register_trace_derivations


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


async def test_derivations_and_khop_cut() -> None:
    g = InMemoryGraphStore()
    await g.register_derivation("v_mail", "v_summary", "run_1")
    await g.register_derivation("v_summary", "v_export", "run_1")
    await g.register_derivation("v_other", "v_far", "run_2")

    one = await g.khop("v_mail", k=1, limit=50)
    assert set(one["nodes"]) == {"v_mail", "v_summary"}

    two = await g.khop("v_mail", k=2, limit=50)
    assert set(two["nodes"]) == {"v_mail", "v_summary", "v_export"}
    assert {"src": "v_mail", "dst": "v_summary", "run_id": "run_1"} in two["edges"]
    assert "v_other" not in two["nodes"]  # focus cut, not the whole graph


async def test_khop_limit_caps_nodes() -> None:
    g = InMemoryGraphStore()
    for i in range(10):
        await g.register_derivation("root", f"v{i}", "r")
    out = await g.khop("root", k=1, limit=3)
    assert len(out["nodes"]) <= 3


async def test_register_trace_derivations_from_events() -> None:
    g = InMemoryGraphStore()
    events = [
        {"kind": "tool_call", "payload": {"tool": "email_read", "arg_refs": {}}},
        {"kind": "tool_result", "payload": {"value_ref": "v_mail"}},
        {"kind": "tool_call",
         "payload": {"tool": "summarize", "arg_refs": {"text": "v_mail"}}},
        {"kind": "tool_result", "payload": {"value_ref": "v_summary"}},
    ]
    n = await register_trace_derivations(g, "run_1", events)
    assert n == 1
    two = await g.khop("v_mail", k=1, limit=50)
    assert {"src": "v_mail", "dst": "v_summary", "run_id": "run_1"} in two["edges"]


async def test_ingest_folds_graph_and_khop_route(client: httpx.AsyncClient) -> None:
    events = [
        {"seq": 0, "kind": "tool_call", "payload": {"tool": "read", "arg_refs": {}}},
        {"seq": 1, "kind": "tool_result", "payload": {"value_ref": "v_a"}},
        {"seq": 2, "kind": "tool_call",
         "payload": {"tool": "x", "arg_refs": {"a": "v_a"}}},
        {"seq": 3, "kind": "tool_result", "payload": {"value_ref": "v_b"}},
    ]
    await client.post("/v1/ingest/run_g", json={"node_id": "n1", "events": events})
    r = await client.get("/v1/graph/khop", params={"focus": "v_a", "k": 2})
    body = r.json()
    assert r.status_code == 200
    assert "v_b" in body["nodes"]
    edge = next(e for e in body["edges"] if e["dst"] == "v_b")
    assert edge["run_id"] == "run_g"  # edge → EvidenceCase link


async def test_seed_adapter_runs_lights_up_deep_surfaces(
    client: httpx.AsyncClient,
) -> None:
    """The seed makes counterfactual divergence, the taint graph, and two-sided
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

    # 3. Taint graph folded the provenance edge.
    g = (await client.get("/v1/graph/khop", params={"focus": "v_mail", "k": 3})).json()
    assert {"src": "v_mail", "dst": "v_sum", "run_id": "ex_block"} in g["edges"]

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


async def test_graph_rehydrates_from_the_event_log(client: httpx.AsyncClient) -> None:
    """The graph is a derived index — after a 'restart' (fresh store + rehydrate)
    it rebuilds the same derivations and attestations from the persisted DB."""
    from axor_backend.graph import InMemoryGraphStore, rehydrate_graph

    events = [
        {"seq": 0, "kind": "tool_call", "payload": {"tool": "r", "arg_refs": {}}},
        {"seq": 1, "kind": "tool_result", "payload": {"value_ref": "v_a"}},
        {"seq": 2, "kind": "tool_call", "payload": {"tool": "x", "arg_refs": {"a": "v_a"}}},
        {"seq": 3, "kind": "tool_result", "payload": {"value_ref": "v_b"}},
    ]
    await client.post("/v1/ingest/run_re", json={"node_id": "n1", "events": events})
    await client.post("/v1/plane/n1/facts", json={"fact": {
        "fact_id": "att_re", "fact_type": "operator_attestation",
        "covers": ["v_a"], "operator": "op", "reason": "checked",
    }})

    # Simulate a restart: a brand-new empty graph, rebuilt from the same store.
    store = client._app.state.store  # type: ignore[attr-defined]
    fresh = InMemoryGraphStore()
    await rehydrate_graph(store, fresh)

    kh = await fresh.khop("v_a", 2, 50)
    assert {"src": "v_a", "dst": "v_b", "run_id": "run_re"} in kh["edges"]
    atts = await fresh.branch_attestations("v_a")
    assert len(atts) == 1 and atts[0]["fact_id"] == "att_re"


async def test_attestation_surface_route(client: httpx.AsyncClient) -> None:
    fact = {
        "fact_id": "att1", "fact_type": "operator_attestation",
        "covers": ["v_mail"], "operator": "op_d", "reason": "checked",
    }
    await client.post("/v1/plane/n1/facts", json={"fact": fact})
    r = await client.get("/v1/graph/attestations", params={"ref": "v_mail"})
    assert r.status_code == 200
    atts = r.json()
    assert len(atts) == 1 and atts[0]["fact_id"] == "att1"
    assert atts[0]["reason"] == "checked"


# ── a call belongs to the node that made it ──────────────────────────────────

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

    @staticmethod
    def _call(node: str, ref: str) -> dict:
        return {"kind": "tool_call", "node_id": node,
                "payload": {"arg_refs": {"a": ref}}}

    @staticmethod
    def _result(node: str, ref: str) -> dict:
        return {"kind": "tool_result", "node_id": node,
                "payload": {"value_ref": ref}}

    async def test_interleaved_nodes_do_not_borrow_each_others_inputs(self) -> None:
        from axor_backend.graph import InMemoryGraphStore, register_trace_derivations

        graph = InMemoryGraphStore()
        registered = await register_trace_derivations(graph, "run1", [
            self._call("A", "v_a"), self._call("B", "v_b"),
            self._result("A", "out_a"), self._result("B", "out_b"),
        ])
        assert registered == 2
        edges = {(e["src"], e["dst"]) for e in (await graph.khop("v_a", 3, 50))["edges"]}
        edges |= {(e["src"], e["dst"]) for e in (await graph.khop("v_b", 3, 50))["edges"]}
        assert edges == {("v_a", "out_a"), ("v_b", "out_b")}

    async def test_a_single_node_trace_is_unchanged(self) -> None:
        from axor_backend.graph import InMemoryGraphStore, register_trace_derivations

        graph = InMemoryGraphStore()
        assert await register_trace_derivations(graph, "run1", [
            self._call("A", "v1"), self._result("A", "v2"),
        ]) == 1

    async def test_a_result_with_no_call_of_its_own_derives_from_nothing(
        self,
    ) -> None:
        """Node B answering without having called claims no provenance, even
        while node A has a call outstanding."""
        from axor_backend.graph import InMemoryGraphStore, register_trace_derivations

        graph = InMemoryGraphStore()
        assert await register_trace_derivations(graph, "run1", [
            self._call("A", "v_a"), self._result("B", "out_b"),
        ]) == 0

    async def test_a_node_consumes_its_pending_inputs_once(self) -> None:
        """The second result on a node whose call was already answered derives
        from nothing — it is a different call, and its own arg_refs would have
        been recorded had it made one."""
        from axor_backend.graph import InMemoryGraphStore, register_trace_derivations

        graph = InMemoryGraphStore()
        assert await register_trace_derivations(graph, "run1", [
            self._call("A", "v_a"), self._result("A", "out1"),
            self._result("A", "out2"),
        ]) == 1


class TestKhopArgumentsAreBoundedBeforeTheStoreSeesThem:
    """`k` and `limit` went from the query string into the store untouched. The
    in-memory store returned an empty neighbourhood for nonsense; Kuzu raised
    out of the driver, so `k=0`, `k=-1`, `k=31` and a negative limit each
    answered 500 to a caller who had only asked for too much."""

    @staticmethod
    async def _client(tmp_path):  # noqa: ANN001, ANN205
        import httpx
        from axor_backend.app import create_app

        app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/g.db",
                         operator_keys={}, allow_unsigned=True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t",
        ) as c, app.router.lifespan_context(app):
            yield c

    async def test_out_of_range_arguments_are_refused_with_a_reason(
        self, tmp_path,  # noqa: ANN001
    ) -> None:
        from axor_backend.limits import MAX_KHOP_K, MAX_KHOP_LIMIT

        async for client in self._client(tmp_path):
            for query in (f"k={MAX_KHOP_K + 1}", "k=0", "k=-1",
                          f"limit={MAX_KHOP_LIMIT + 1}", "limit=0", "limit=-5"):
                r = await client.get(f"/v1/graph/khop?focus=v1&{query}")
                assert r.status_code == 422, (query, r.status_code)

    async def test_the_bounds_themselves_are_accepted(
        self, tmp_path,  # noqa: ANN001
    ) -> None:
        from axor_backend.limits import MAX_KHOP_K, MAX_KHOP_LIMIT

        async for client in self._client(tmp_path):
            r = await client.get(
                f"/v1/graph/khop?focus=v1&k={MAX_KHOP_K}&limit={MAX_KHOP_LIMIT}")
            assert r.status_code == 200

    async def test_kuzu_can_serve_every_k_the_route_allows(
        self, tmp_path,  # noqa: ANN001
    ) -> None:
        """The cap is not a guess: Kuzu refuses a variable-length pattern longer
        than 30, so a route that allowed more would be promising something the
        hosted store cannot do."""
        import pytest

        pytest.importorskip("kuzu")
        from axor_backend.graph import KuzuGraphStore
        from axor_backend.limits import MAX_KHOP_K

        store = KuzuGraphStore(tmp_path, "t")
        await store.register_derivation("v1", "v2", "r")
        assert (await store.khop("v1", MAX_KHOP_K, 100))["nodes"] == ["v1", "v2"]


async def test_folding_an_attestation_again_does_not_double_it() -> None:
    """Parity with the Kuzu store: `fact_id` is the identity of an attestation
    in both, so folding the fact log twice is folding it once. The in-memory
    store kept a list and appended, which read as correct only because nothing
    re-folded in one process."""
    import json

    from axor_backend.graph import InMemoryGraphStore

    graph = InMemoryGraphStore()
    fact = json.dumps({"fact_id": "f1", "operator": "op", "reason": "checked",
                       "covers": ["v2"]})
    for _ in range(3):
        await graph.append_attestation(fact)
    assert len(await graph.branch_attestations("v2")) == 1


async def test_two_attestations_on_one_branch_are_both_kept() -> None:
    """Deduplication is on the fact id, not the branch: two operators attesting
    the same value are two facts, and an audit trail that collapses them is
    worse than none."""
    import json

    from axor_backend.graph import InMemoryGraphStore

    graph = InMemoryGraphStore()
    for fact_id, operator in (("f1", "alice"), ("f2", "bob")):
        await graph.append_attestation(json.dumps({
            "fact_id": fact_id, "operator": operator, "reason": "checked",
            "covers": ["v2"]}))
    assert {a["operator"] for a in await graph.branch_attestations("v2")} == {
        "alice", "bob"}
