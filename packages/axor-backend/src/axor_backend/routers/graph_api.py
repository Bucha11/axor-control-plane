"""The taint / provenance graph (spec decision 6).

A derived index over the persisted event log, held per tenant and rebuilt from
the log at boot — so it survives a restart without a graph database being part
of the deployment.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from axor_backend.deps import GraphDep
from axor_backend.limits import MAX_KHOP_K, MAX_KHOP_LIMIT

router = APIRouter(prefix="/v1/graph", tags=["graph"])


@router.get("/khop")
async def graph_khop(
    graph: GraphDep,
    focus: str,
    k: int = Query(2, ge=1, le=MAX_KHOP_K),
    limit: int = Query(100, ge=1, le=MAX_KHOP_LIMIT),
) -> dict:
    """k-hop neighbourhood around a value ref, expand-on-click. Each edge
    carries the run_id it was derived in — the UI links an edge back to that
    run's EvidenceCase.

    `k` and `limit` are bounded here rather than trusted. Unbounded, they went
    straight into the graph store: on Kuzu, `k=0`, `k=-1`, `k=31` and a negative
    limit each raised out of the driver and answered 500 to a caller who had
    only asked for too much, while the in-memory store returned an empty
    neighbourhood for the same input. Two implementations of one Protocol
    disagreeing about what a bad argument means is what a bound at the edge
    removes: neither ever sees one.
    """
    return await graph.khop(focus, k, limit)


@router.get("/attestations")
async def graph_attestations(graph: GraphDep, ref: str) -> list[dict]:
    """Attestations covering a value branch (spec 8.1.1)."""
    return await graph.branch_attestations(ref)
