"""Reading a case back: causal subgraph, containment, influence, replay.

Every route here is a pure kernel computation over stored events — nothing is
written, and no verdict is invented. That is why they read with the `read`
scope even when they are POSTs: a counterfactual and an influence ranking are
questions asked of a trace, not changes to it.
"""
from __future__ import annotations

from axor_core.kernel.replay import replay
from axor_core.kernel.subgraph import causal_subgraph
from fastapi import APIRouter, HTTPException

from axor_backend.deps import StoreDep, SubgraphCacheDep
from axor_backend.replay_api import (
    containment_report,
    influence_ranking,
    kernel_config_from_json,
    scrubber_payload,
)
from axor_backend.tenancy import current_org_id
from axor_backend.traces import events_for

router = APIRouter(prefix="/v1", tags=["analysis"])


def _subgraph(events: list, anchor_node: str, anchor_seq: int) -> dict:
    try:
        return causal_subgraph(events, anchor_node, anchor_seq)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/runs/{run_id}/subgraph")
async def run_subgraph(
    run_id: str,
    anchor_node: str,
    anchor_seq: int,
    store: StoreDep,
    cache: SubgraphCacheDep,
) -> dict:
    """The causal subgraph for a case (spec v2 Ch.3): computed on open by the
    pure kernel walk, cached, never stored redundantly."""
    org = current_org_id()
    cached = cache.get(org, run_id, anchor_node, anchor_seq)
    if cached is not None:
        return cached
    events = await events_for(store, run_id)
    return cache.put(
        org, run_id, anchor_node, anchor_seq,
        _subgraph(events, anchor_node, anchor_seq),
    )


@router.get("/runs/{run_id}/containment")
async def run_containment(
    run_id: str, anchor_node: str, anchor_seq: int, store: StoreDep
) -> dict:
    """Containment metric + systemic outcome for a case (spec v2 Ch.2):
    event-grounded (headline-safe) ratio, outcome as a label — never a
    governance-attributed score."""
    events = await events_for(store, run_id)
    return containment_report(events, _subgraph(events, anchor_node, anchor_seq))


@router.post("/runs/{run_id}/influence")
async def run_influence(run_id: str, body: dict, store: StoreDep) -> dict:
    """Influence ranking by subgraph ablation (spec v2 Ch.3 §7): which upstream
    value most drove the anchor's claim. Deterministic; bounded by causal-chain
    length."""
    anchor_node = body.get("anchor_node", "")
    anchor_seq = int(body.get("anchor_seq", -1))
    events = await events_for(store, run_id)
    sub = _subgraph(events, anchor_node, anchor_seq)
    case_nodes = {n["node_id"] for n in sub["nodes"]}
    refs = sorted({
        str(e.payload.get("value_ref"))
        for e in events
        if e.node_id in case_nodes and e.payload.get("value_ref")
    })
    cfg_json = body.get("config") or {}
    if not cfg_json:
        # Default ablation config: the anchor's own denied sink declared as
        # egress — the minimal config under which the recorded containment
        # reproduces, so ablation measures exactly "did this value drive the
        # denial".
        anchor_ev = next(
            e for e in events if e.node_id == anchor_node and e.seq == anchor_seq
        )
        tool = str(anchor_ev.payload.get("tool", ""))
        cfg_json = {"egress_sinks": [tool] if tool else []}
    config = kernel_config_from_json(cfg_json)
    return {
        "anchor": sub["anchor"],
        "ranking": influence_ranking(
            events, config, anchor_node, anchor_seq, refs
        ),
    }


@router.get("/replay/{run_id}")
async def replay_scrubber(run_id: str, store: StoreDep) -> dict:
    return scrubber_payload(replay(await events_for(store, run_id)))


@router.post("/replay/{run_id}")
async def replay_counterfactual(run_id: str, body: dict, store: StoreDep) -> dict:
    events = await events_for(store, run_id)
    config = kernel_config_from_json(body.get("config", {}))
    return scrubber_payload(replay(events, config))
