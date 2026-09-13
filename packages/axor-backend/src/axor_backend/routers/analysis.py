"""Reading a case back: causal subgraph, containment, influence, replay.

Every route here is a pure kernel computation over stored events — nothing is
written, and no verdict is invented. That is why they read with the `read`
scope even when they are POSTs: a counterfactual and an influence ranking are
questions asked of a trace, not changes to it.

Two things follow from "pure kernel computation", and neither was handled.

**It is synchronous CPU work.** Called straight from an ``async def`` it holds
the event loop for its whole duration, and this backend is single-instance by
design (docs/ops-limits.md), so there is no second worker to take over. One
influence request over a 601-event run starved the loop for 3.1 continuous
seconds — measured as the overshoot of 50 ms timers that could not fire:

    /v1/healthz idle:                 0.87 ms
    the influence request took:       3627 ms (601 events)
    worst 50 ms timer overshoot:      3124 ms

Nothing else was served in that window: not ingest, not the plane, not
``/v1/healthz`` — which docker-compose polls with ``timeout: 5s`` and which two
services gate their start on. Every kernel call here now runs through
``asyncio.to_thread``; the GIL still has to be shared, but the interpreter
switches out of it, so a long fold no longer means a dead process.

**Ablation is not linear.** See ``limits.MAX_ABLATION_REFS`` for the numbers.

And the door was typed in one field out of three: ``config`` was checked and
answered 400, while ``anchor_seq`` and ``anchor_node`` went to the kernel as
they arrived — ``int("abc")``, ``int({})`` and an unhashable node id each left
the route as a 500 with ``{"error": "internal"}``, the caller's mistake
reported as ours.
"""
from __future__ import annotations

from axor_core.kernel.events import Event
from axor_core.kernel.replay import replay
from axor_core.kernel.subgraph import causal_subgraph
from fastapi import APIRouter, HTTPException

from axor_backend.deps import StoreDep, SubgraphCacheDep
from axor_backend.limits import MAX_ABLATION_REFS
from axor_backend.offload import off_loop as _off_loop
from axor_backend.replay_api import (
    containment_report,
    influence_ranking,
    kernel_config_from_json,
    scrubber_payload,
)
from axor_backend.tenancy import current_org_id
from axor_backend.traces import events_for

router = APIRouter(prefix="/v1", tags=["analysis"])


def _anchor(body: dict) -> tuple[str, int]:
    """The (node, seq) a POST body names, or a 400 saying which field is wrong.

    `int(body.get("anchor_seq", -1))` raised on a word and on a dict, and a
    non-string node id raised `unhashable type` deeper in the kernel walk. All
    three reached the caller as 500 {"error": "internal"}.
    """
    node = body.get("anchor_node", "")
    if not isinstance(node, str):
        raise HTTPException(400, "anchor_node must be a string")
    raw = body.get("anchor_seq", -1)
    if isinstance(raw, bool) or not isinstance(raw, int):
        # bool is an int in Python and `anchor_seq: true` is not a sequence
        # number; refusing it here beats anchoring at 1.
        raise HTTPException(400, "anchor_seq must be an integer")
    return node, raw


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
        await _off_loop(_subgraph, events, anchor_node, anchor_seq),
    )


@router.get("/runs/{run_id}/containment")
async def run_containment(
    run_id: str, anchor_node: str, anchor_seq: int, store: StoreDep
) -> dict:
    """Containment metric + systemic outcome for a case (spec v2 Ch.2):
    event-grounded (headline-safe) ratio, outcome as a label — never a
    governance-attributed score."""
    events = await events_for(store, run_id)
    sub = await _off_loop(_subgraph, events, anchor_node, anchor_seq)
    return await _off_loop(containment_report, events, sub)


def _requested_refs(body: dict, available: list[str]) -> list[str]:
    """The refs to ablate: the caller's subset, or all of them if they named none.

    A case with more upstream values than the bound is refused, not truncated.
    Ranking the first N by ref id would answer "of these arbitrary values, which
    drove the denial" while being read as "which value drove the denial", and
    the caller has no way to see the substitution happened.
    """
    asked = body.get("refs")
    if asked is not None:
        if not isinstance(asked, list) or not all(isinstance(r, str) for r in asked):
            raise HTTPException(400, "refs must be a list of value-ref strings")
        unknown = sorted(set(asked) - set(available))
        if unknown:
            raise HTTPException(
                400, f"refs not in this case's causal subgraph: {unknown[:10]}")
        refs = [r for r in available if r in set(asked)]
    else:
        refs = available
    if len(refs) > MAX_ABLATION_REFS:
        raise HTTPException(
            422,
            f"this case has {len(refs)} upstream values and one request ablates "
            f"at most {MAX_ABLATION_REFS}: ablation replays the anchor's whole "
            f"local sequence once per ref, so the cost grows about sixfold for "
            f"every doubling. Name the values you want ranked in `refs`, or "
            f"anchor nearer the denial.",
        )
    return refs


@router.post("/runs/{run_id}/influence")
async def run_influence(run_id: str, body: dict, store: StoreDep) -> dict:
    """Influence ranking by subgraph ablation (spec v2 Ch.3 §7): which upstream
    value most drove the anchor's claim. Deterministic; bounded by
    `limits.MAX_ABLATION_REFS`, which is a bound the route enforces rather than
    the causal-chain length it used to hope for."""
    anchor_node, anchor_seq = _anchor(body)
    events = await events_for(store, run_id)
    sub = await _off_loop(_subgraph, events, anchor_node, anchor_seq)
    case_nodes = {n["node_id"] for n in sub["nodes"]}
    available = sorted({
        str(e.payload.get("value_ref"))
        for e in events
        if e.node_id in case_nodes and e.payload.get("value_ref")
    })
    refs = _requested_refs(body, available)
    cfg_json = body.get("config") or {}
    if not cfg_json:
        # Default ablation config: the anchor's own denied sink declared as
        # egress — the minimal config under which the recorded containment
        # reproduces, so ablation measures exactly "did this value drive the
        # denial".
        anchor_ev = _anchor_event(events, anchor_node, anchor_seq)
        tool = str(anchor_ev.payload.get("tool", ""))
        cfg_json = {"egress_sinks": [tool] if tool else []}
    config = kernel_config_from_json(cfg_json)
    return {
        "anchor": sub["anchor"],
        "ablated_refs": len(refs),
        "available_refs": len(available),
        "ranking": await _off_loop(
            influence_ranking, events, config, anchor_node, anchor_seq, refs
        ),
    }


def _anchor_event(events: list[Event], node: str, seq: int) -> Event:
    """The anchor itself. `next(...)` with no default raised StopIteration out
    of a coroutine, which Python re-raises as a bare RuntimeError — a 500 for a
    case the subgraph walk happens to admit but that carries no such event."""
    for event in events:
        if event.node_id == node and event.seq == seq:
            return event
    raise HTTPException(404, f"no event seq={seq} at node {node!r}")


def _replayed(events: list[Event], config: object = None) -> dict:
    """Fold and shape, as one unit of work handed to a thread.

    `scrubber_payload` is another pass over every step, so leaving it behind
    while `replay` went off the loop kept two thirds of the block: measured on a
    60 000-event run, replay 0.63 s, scrubber_payload 0.62 s.
    """
    return scrubber_payload(replay(events) if config is None else replay(events, config))


@router.get("/replay/{run_id}")
async def replay_scrubber(run_id: str, store: StoreDep) -> dict:
    # What still costs the loop here is FastAPI serialising the answer: 0.38 s
    # for the 22 MB a 60 000-event run produces. That is not work a thread can
    # take — the fix for it is not returning 22 MB, which is `MAX_EVENTS_PER_RUN`'s
    # job, not this route's.
    return await _off_loop(_replayed, await events_for(store, run_id))


@router.post("/replay/{run_id}")
async def replay_counterfactual(run_id: str, body: dict, store: StoreDep) -> dict:
    events = await events_for(store, run_id)
    config = kernel_config_from_json(body.get("config", {}))
    return await _off_loop(_replayed, events, config)
