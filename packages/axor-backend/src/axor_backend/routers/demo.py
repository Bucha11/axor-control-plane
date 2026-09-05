"""Canned runs the proxy cannot produce.

Adapter-depth traces carry recorded verdicts and value provenance; a proxy sees
neither. These two routes seed exactly that depth so counterfactual divergence,
the taint graph and two-sided regression can be demonstrated in-app against real
stored events rather than a mock.

Idempotent by construction: re-seeding overwrites the same run ids.
"""
from __future__ import annotations

from fastapi import APIRouter

from axor_backend.clock import now
from axor_backend.deps import GraphDep, StoreDep, SubgraphCacheDep
from axor_backend.graph import register_trace_derivations
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1/demo", tags=["demo"])


@router.post("/seed-adapter-runs")
async def seed_adapter_runs(
    store: StoreDep, graph: GraphDep, cache: SubgraphCacheDep
) -> dict:
    """Two adapter-fidelity runs: one denied at the boundary, one clean."""
    from axor_backend import demo

    for run_id, node, events, evidence, pin_side in (
        ("ex_block", demo.EX_BLOCK_NODE, demo.EX_BLOCK_EVENTS,
         demo.EX_BLOCK_EVIDENCE, "must_block"),
        ("ex_pass", demo.EX_PASS_NODE, demo.EX_PASS_EVENTS, [], "must_pass"),
    ):
        await store.upsert_run(run_id, node, "adapter-demo", now())
        await store.ingest_events(run_id, node, events, f"seed-{run_id}")
        await register_trace_derivations(graph, run_id, events)
        cache.drop_run(current_org_id(), run_id)
        if evidence:
            await store.set_evidence(run_id, evidence)
        # Pin both corpus sides explicitly (idempotent) — set_evidence's
        # auto-pin lives in the HTTP route, which this seed bypasses.
        await store.pin(run_id, pin_side, "adapter-demo")
    return {"seeded": ["ex_block", "ex_pass"], "config": demo.EX_CONFIG}


@router.post("/seed-tree-run")
async def seed_tree_run(
    store: StoreDep, graph: GraphDep, cache: SubgraphCacheDep
) -> dict:
    """The canned multi-agent tree run (spec v2): 4 nodes, carried taint up two
    delegation hops, one lateral edge, export denied at the orchestrator — the
    topology graph, the causal subgraph and the two-tree containment story all
    read from this one trace."""
    from axor_backend import demo

    await store.upsert_run("ex_tree", demo.TREE_ORCH, "multi-agent-demo", now())
    await store.ingest_events(
        "ex_tree", demo.TREE_ORCH, demo.TREE_EVENTS, "seed-ex_tree"
    )
    await register_trace_derivations(graph, "ex_tree", demo.TREE_EVENTS)
    cache.drop_run(current_org_id(), "ex_tree")
    await store.set_evidence("ex_tree", demo.TREE_EVIDENCE)
    return {"seeded": ["ex_tree"], "config": demo.TREE_CONFIG}
