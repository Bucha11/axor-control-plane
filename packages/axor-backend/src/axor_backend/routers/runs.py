"""Run ingest and read — the upload path and the live audit stream.

A trace arriving here becomes three things at once: rows in the event log, edges
in the tenant's provenance graph, and messages on the SSE bus. The log is the
system of record; the other two are derived, and both are rebuilt from it at
boot (``rehydrate_all_graphs``) — so a failure after the write leaves the
process stale, never the data wrong.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header
from sse_starlette.sse import EventSourceResponse

from axor_backend.clock import now
from axor_backend.deps import (
    BroadcastDep,
    GraphDep,
    NotifierDep,
    StoreDep,
    SubgraphCacheDep,
)
from axor_backend.graph import register_trace_derivations
from axor_backend.limits import check_batch_size
from axor_backend.tenancy import current_org_id, topic

router = APIRouter(prefix="/v1", tags=["runs"])


@router.post("/ingest/{run_id}", status_code=202)
async def ingest(
    run_id: str,
    body: dict,
    store: StoreDep,
    graph: GraphDep,
    bus: BroadcastDep,
    cache: SubgraphCacheDep,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    node_id = body.get("node_id", "proxy")
    events = check_batch_size(body.get("events", []))
    await store.upsert_run(run_id, node_id, body.get("scenario", "custom"), now())
    stored = await store.ingest_events(run_id, node_id, events, idempotency_key)
    # Fold the trace's value provenance into the taint graph (spec decision 6).
    await register_trace_derivations(graph, run_id, events)
    # This run's causal subgraphs were derived from a shorter event list.
    cache.drop_run(current_org_id(), run_id)
    for line in events:
        bus.publish(topic("run", run_id), {"type": "event", "line": line})
    return {"stored": stored}


@router.post("/runs/{run_id}/evidence")
async def set_evidence(
    run_id: str, body: dict, store: StoreDep, notifier: NotifierDep
) -> dict:
    evidence = body.get("evidence", [])
    await store.set_evidence(run_id, evidence)
    # Run completed with >=1 EvidenceCase → notify (spec section 16 trigger).
    deviations = [c for c in evidence if c.get("deviation")]
    if deviations:
        # Auto-pin the must-block side here, at the system of record (decision
        # 11): a trace carrying an EvidenceCase IS the regression corpus's block
        # side, and pinning it should not depend on the uploading client
        # remembering to POST /v1/pins. pin() is idempotent, so the proxy's own
        # pin call stays a harmless no-op.
        await store.pin(run_id, "must_block", body.get("scenario", ""))
        await notifier.emit(
            "evidence_run",
            body.get("node_id", "proxy"),
            {
                "run_id": run_id,
                "cases": len(deviations),
                "permalink": f"/v1/runs/{run_id}",
            },
        )
    return {"ok": True, "notified": bool(deviations)}


@router.get("/runs")
async def list_runs(store: StoreDep) -> list[dict]:
    return await store.list_runs()


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, store: StoreDep) -> list[dict]:
    lines = await store.run_events(run_id)
    return [json.loads(line) for line in lines]


@router.get("/runs/{run_id}/stream")
async def run_stream(
    run_id: str,
    store: StoreDep,
    bus: BroadcastDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Live colour-coded audit stream (spec section 8): replay from
    Last-Event-ID (seq), then live events."""
    # Bind the tenant before the body starts streaming: the generator below is
    # iterated after this handler returns, and the ambient org is not guaranteed
    # to still be set by then.
    run_topic = topic("run", run_id, current_org_id())
    queue = bus.subscribe(run_topic)

    async def stream() -> AsyncIterator[dict]:
        try:
            # A malformed Last-Event-ID must not kill the stream — replay all.
            try:
                after = int(last_event_id) if last_event_id else -1
            except ValueError:
                after = -1
            for line in await store.run_events(run_id, after_seq=after):
                parsed = json.loads(line)
                yield {"event": "event", "id": str(parsed["seq"]), "data": line}
            while True:
                message = await queue.get()
                line_dict = message["line"]
                yield {
                    "event": "event",
                    "id": str(line_dict.get("seq", "")),
                    "data": json.dumps(line_dict, sort_keys=True),
                }
        finally:
            bus.unsubscribe(run_topic, queue)

    return EventSourceResponse(stream())
