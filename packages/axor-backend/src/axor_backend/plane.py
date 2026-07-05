"""Plane service endpoints (protocol note v0.2).

GET  /v1/plane/{node_id}/desired    SSE: `snapshot` first, then deltas
POST /v1/plane/{node_id}/telemetry  batched kernel events, Idempotency-Key dedup
POST /v1/plane/{node_id}/command    declarative desired-state write, version++
POST /v1/plane/{node_id}/facts      append-only facts (attestations)
POST /v1/plane/{node_id}/consumed   one-shot consumption ack (injection/excision)

Merge/absorb semantics live in axor_core.kernel.state.DesiredState — the
backend persists and fans out; it does not interpret. Signature verification
here is defense in depth only: the adapter re-verifies with operator pubkeys
from ITS OWN config (protocol, section 6). Command versioning is optimistic:
the operator signs (node_id, version=current+1, body, timestamp); a stale
version is rejected and the operator retries against the fresh version.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from axor_backend.errors import CommandRejected
from axor_backend.signing import signed_payload

router = APIRouter(prefix="/v1/plane")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ctx(request: Request) -> Any:  # noqa: ANN401 - app.state is dynamic
    return request.app.state


@router.post("/{node_id}/command", status_code=202)
async def command(node_id: str, body: dict, request: Request) -> dict:
    ctx = _ctx(request)
    delta = body.get("state")
    if not isinstance(delta, dict) or not delta:
        raise HTTPException(400, "command requires a non-empty `state` delta")
    version = body.get("version")
    operator = body.get("operator", "")
    timestamp = body.get("timestamp", "")
    sig = body.get("sig", "")

    current = await ctx.store.get_desired(node_id)
    expected = (current[0] if current else 0) + 1
    if version != expected:
        raise HTTPException(409, f"stale version {version}; expected {expected}")
    if not ctx.keyring.empty:
        try:
            ctx.keyring.verify(
                operator, signed_payload(node_id, version, delta, timestamp), sig
            )
        except CommandRejected as exc:
            raise HTTPException(403, str(exc)) from exc
    elif not ctx.allow_unsigned:
        raise HTTPException(403, "no operator keys registered; commands rejected")

    new_version, state = await ctx.store.bump_desired(node_id, delta)
    message = {
        "type": "delta", "node_id": node_id, "version": new_version,
        "state": state, "delta": delta, "operator": operator,
        "timestamp": timestamp, "sig": sig,
    }
    ctx.broadcast.publish(f"plane:{node_id}", message)
    return {"node_id": node_id, "version": new_version, "state": state}


@router.get("/{node_id}/desired")
async def desired_stream(node_id: str, request: Request) -> EventSourceResponse:
    ctx = _ctx(request)
    queue = ctx.broadcast.subscribe(f"plane:{node_id}")

    async def stream() -> AsyncIterator[dict]:
        try:
            current = await ctx.store.get_desired(node_id)
            version, state = current if current else (0, {})
            yield {"event": "snapshot",
                   "data": _json({"node_id": node_id, "version": version,
                                  "state": state})}
            while True:
                message = await queue.get()
                yield {"event": message.get("type", "delta"),
                       "data": _json(message)}
        finally:
            ctx.broadcast.unsubscribe(f"plane:{node_id}", queue)

    return EventSourceResponse(stream())


@router.post("/{node_id}/telemetry", status_code=202)
async def telemetry(
    node_id: str,
    body: dict,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    ctx = _ctx(request)
    run_id = body.get("run_id", node_id)
    lines: list[dict[str, Any]] = body.get("events", [])
    await ctx.store.upsert_run(run_id, node_id, body.get("scenario", "live"), _now())
    stored = await ctx.store.ingest_events(run_id, node_id, lines, idempotency_key)
    notifier = getattr(ctx, "notifier", None)
    _LEVELS = {"NORMAL": 0, "CAUTIOUS": 1, "RESTRICTED": 2, "LOCKED": 3, "TERMINAL": 4}
    for line in lines:
        kind = line.get("kind")
        if kind == "heartbeat":
            hb = line.get("payload", {})
            prior = await ctx.store.get_reported(node_id)
            await ctx.store.upsert_reported(
                node_id,
                applied_version=int(hb.get("applied_version", 0)),
                level=str(hb.get("level", "NORMAL")),
                budget_remaining=hb.get("budget_remaining"),
                ts=_now(),
            )
            ctx.broadcast.publish(
                f"plane:{node_id}",
                {"type": "reported", "node_id": node_id, "reported": hb},
            )
            # Notify on an upward level transition (spec section 16 trigger).
            new_level = str(hb.get("level", "NORMAL"))
            old_level = prior["level"] if prior else "NORMAL"
            if notifier is not None and _LEVELS.get(new_level, 0) > _LEVELS.get(old_level, 0):
                await notifier.emit(
                    "level_transition_up", node_id,
                    {"from": old_level, "to": new_level,
                     "permalink": f"/v1/plane/nodes#{node_id}"},
                )
        if kind == "operator_intervention":
            await ctx.store.mark_intervened(run_id)
        ctx.broadcast.publish(f"run:{run_id}", {"type": "event", "line": line})
    return {"stored": stored}


@router.post("/{node_id}/facts", status_code=201)
async def append_fact(node_id: str, body: dict, request: Request) -> dict:
    ctx = _ctx(request)
    fact = body.get("fact")
    if not isinstance(fact, dict) or "fact_id" not in fact:
        raise HTTPException(400, "requires `fact` with fact_id")
    operator = body.get("operator", "")
    timestamp = body.get("timestamp", "")
    sig = body.get("sig", "")
    if fact.get("fact_type") == "operator_attestation" and not fact.get("reason"):
        raise HTTPException(400, "attestation requires a reason (decision 8)")
    if not ctx.keyring.empty:
        try:
            ctx.keyring.verify(
                operator, signed_payload(node_id, 0, fact, timestamp), sig
            )
        except CommandRejected as exc:
            raise HTTPException(403, str(exc)) from exc
    elif not ctx.allow_unsigned:
        raise HTTPException(403, "no operator keys registered; facts rejected")
    appended = await ctx.store.append_fact(node_id, fact, _now())
    if not appended:
        raise HTTPException(409, "fact_id already exists (append-only)")
    # An operator attestation is an append-only node over the branch it covers
    # (spec 8.1.1) — mirror it into the taint graph so the graph's attestation
    # surface and the fact log stay one story.
    graph = getattr(ctx, "graph", None)
    if graph is not None and fact.get("fact_type") == "operator_attestation":
        import json as _json
        await graph.append_attestation(_json.dumps(fact))
    ctx.broadcast.publish(
        f"plane:{node_id}",
        {"type": "fact", "node_id": node_id, "fact": fact,
         "operator": operator, "timestamp": timestamp, "sig": sig},
    )
    # Sentinel records a branch crossing its suspicion threshold as an appended
    # heat_crossing fact (heat is Sentinel's, not the runtime's — it arrives here
    # as evidence, signed like any other fact). That crossing is a spec §16
    # trigger, so fire the notification when the fact says the threshold was met.
    notifier = getattr(ctx, "notifier", None)
    if (
        notifier is not None
        and fact.get("fact_type") == "heat_crossing"
        and float(fact.get("score", 0.0)) >= float(fact.get("threshold", 1.0))
    ):
        await notifier.emit(
            "heat_threshold", node_id,
            {"score": float(fact["score"]),
             "threshold": float(fact.get("threshold", 1.0)),
             "resource_id": fact.get("resource_id", ""),
             "permalink": f"/v1/plane/nodes#{node_id}"},
        )
    return {"appended": True}


@router.post("/{node_id}/consumed", status_code=200)
async def consumed(node_id: str, body: dict, request: Request) -> dict:
    ctx = _ctx(request)
    key = body.get("key")
    if key not in ("pending_injection", "pending_excision"):
        raise HTTPException(400, "key must be pending_injection|pending_excision")
    await ctx.store.clear_desired_key(node_id, key)
    return {"cleared": key}


@router.get("/nodes")
async def nodes(request: Request) -> list[dict]:
    """Topology data: desired next to reported — divergence is rendered, not
    hidden (protocol, section 5)."""
    ctx = _ctx(request)
    out = []
    for node_id in await ctx.store.list_nodes():
        current = await ctx.store.get_desired(node_id)
        out.append({
            "node_id": node_id,
            "desired": (
                {"version": current[0], "state": current[1]} if current else None
            ),
            "reported": await ctx.store.get_reported(node_id),
            "facts": await ctx.store.node_facts(node_id),
        })
    return out


def _json(value: dict) -> str:
    return json.dumps(value, sort_keys=True)
