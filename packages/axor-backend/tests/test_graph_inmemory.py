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
