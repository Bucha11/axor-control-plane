"""M3: derive-on-open causal subgraph + influence ranking by ablation.

Spec v2 Ch.3 — the subgraph is derived from the trace on case open (cached,
never stored); influence is subgraph ablation through the kernel fold.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={},
        allow_unsigned=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c:
        async with app.router.lifespan_context(app):
            await c.post("/v1/demo/seed-tree-run")
            yield c


async def test_subgraph_is_minimal_causes_not_org_chart(
    client: httpx.AsyncClient,
) -> None:
    sub = (await client.get(
        "/v1/runs/ex_tree/subgraph",
        params={"anchor_node": "tree-orch", "anchor_seq": 4},
    )).json()
    ids = {n["node_id"] for n in sub["nodes"]}
    # tree-writer took a lateral message but did not feed the claim — excluded
    assert ids == {"tree-scraper", "tree-research", "tree-orch"}
    roles = {n["node_id"]: set(n["roles"]) for n in sub["nodes"]}
    assert "origin" in roles["tree-scraper"]
    assert "conduit" in roles["tree-research"]
    assert {"anchor", "container"} <= roles["tree-orch"]
    assert sub["fault_origin"]["node_id"] == "tree-scraper"
    assert sub["contained_at"]  # the denial IS the case (v2-11)
    assert sub["federation_scope"] == "intra"


async def test_subgraph_unknown_anchor_404(client: httpx.AsyncClient) -> None:
    r = await client.get(
        "/v1/runs/ex_tree/subgraph",
        params={"anchor_node": "tree-orch", "anchor_seq": 99},
    )
    assert r.status_code == 404


async def test_influence_ranks_the_fabricated_value_first(
    client: httpx.AsyncClient,
) -> None:
    body = {
        "anchor_node": "tree-orch", "anchor_seq": 4,
        "config": {"allowed_tools": ["web_search", "summarize", "slack_post"],
                   "egress_sinks": ["slack_post"]},
    }
    out = (await client.post("/v1/runs/ex_tree/influence", json=body)).json()
    ranking = out["ranking"]
    assert ranking, "expected a non-empty ranking"
    top = ranking[0]
    # Ablating the carried fabrication flips the anchor's DENY — it drove
    # the case; deterministic, no model involved.
    assert top["ref"] == "v_sum"
    assert top["influence"] == 1.0
    assert top["baseline_verdict"] == "deny"
    assert top["ablated_verdict"] == "pass"


async def test_tree_evidence_carries_anchor_for_derive_on_open(
    client: httpx.AsyncClient,
) -> None:
    runs = (await client.get("/v1/runs")).json()
    tree = next(r for r in runs if r["run_id"] == "ex_tree")
    case = tree["evidence"][0]
    assert case["deviation"] == "fabrication_contained"
    assert case["anchor"] == {"node_id": "tree-orch", "seq": 4}
    # v0.13 fields intact — one format, two renders (Ch.3 §7)
    for k in ("scenario", "verdict_source", "confidence",
              "observed_reality", "agent_claim", "fault_attribution"):
        assert k in case
