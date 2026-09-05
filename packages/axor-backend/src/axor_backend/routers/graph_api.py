"""The taint / provenance graph (spec decision 6).

A derived index over the persisted event log, held per tenant and rebuilt from
the log at boot — so it survives a restart without a graph database being part
of the deployment.
"""
from __future__ import annotations

from fastapi import APIRouter

from axor_backend.deps import GraphDep

router = APIRouter(prefix="/v1/graph", tags=["graph"])


@router.get("/khop")
async def graph_khop(graph: GraphDep, focus: str, k: int = 2, limit: int = 100) -> dict:
    """k-hop neighbourhood around a value ref, expand-on-click. Each edge
    carries the run_id it was derived in — the UI links an edge back to that
    run's EvidenceCase."""
    return await graph.khop(focus, k, limit)


@router.get("/attestations")
async def graph_attestations(graph: GraphDep, ref: str) -> list[dict]:
    """Attestations covering a value branch (spec 8.1.1)."""
    return await graph.branch_attestations(ref)
