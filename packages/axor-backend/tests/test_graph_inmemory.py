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
