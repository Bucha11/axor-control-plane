"""Plane service endpoints (protocol note v0.2).

GET  /v1/plane/{node_id}/desired    SSE: `snapshot` first, then deltas
POST /v1/plane/{node_id}/telemetry  batched kernel events, Idempotency-Key dedup
POST /v1/plane/{node_id}/command    declarative desired-state write, version++
POST /v1/plane/{node_id}/facts      append-only facts (attestations)
POST /v1/plane/{node_id}/consumed   one-shot consumption ack (injection/excision)
POST /v1/plane/{node_id}/probe-report  behavioral health check posted by the node
GET  /v1/plane/{node_id}/probe-report  last check + the series behind it

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
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from axor_backend.clock import now, today
from axor_backend.errors import (
    CommandRejected,
    ConcurrentUpdate,
    StaleVersion,
)
from axor_backend.limits import check_batch_size
from axor_backend.signing import signed_payload
from axor_backend.tenancy import current_org_id, topic

router = APIRouter(prefix="/v1/plane")

# The degradation ladder, as an order. A node climbs it on its own; the plane
# only records where it says it is, and pages on the way UP.
_LEVELS = {"NORMAL": 0, "CAUTIOUS": 1, "RESTRICTED": 2, "LOCKED": 3, "TERMINAL": 4}


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

    # The store re-checks the version it was signed for, atomically. The 409
    # above is the friendly early answer; this is the one that actually holds
    # when two commands arrive together.
    new_version, state = await _apply(ctx, node_id, delta, expect_version=version - 1)
    message = {
        "type": "delta", "node_id": node_id, "version": new_version,
        "state": state, "delta": delta, "operator": operator,
        "timestamp": timestamp, "sig": sig,
    }
    ctx.broadcast.publish(topic("plane", node_id), message)
    return {"node_id": node_id, "version": new_version, "state": state}


@router.post("/{node_id}/cascade-stop", status_code=202)
async def cascade_stop(node_id: str, request: Request, body: dict | None = None) -> dict:
    """Stop a node and its whole subtree (spec §12 cascade stop; spec v2 Ch.4
    §6). Signed deployments: ONE signed command to the subtree root carrying
    `{"stopped": true, "cascade": true}` — the plane commands the root, the
    tree distributes the signal child-ward along spawn edges (the plane may
    not even know the live shape between heartbeats). Unsigned/open posture
    keeps the legacy backend-side BFS over the self-reported `parent` field
    (deprecated: structure should derive from traced spawn events)."""
    ctx = _ctx(request)
    if not ctx.keyring.empty:
        body = body or {}
        delta = {"stopped": True, "cascade": True}
        version = body.get("version")
        current = await ctx.store.get_desired(node_id)
        expected = (current[0] if current else 0) + 1
        if version != expected:
            raise HTTPException(409, f"stale version {version}; expected {expected}")
        try:
            ctx.keyring.verify(
                body.get("operator", ""),
                signed_payload(node_id, version, delta, body.get("timestamp", "")),
                body.get("sig", ""),
            )
        except CommandRejected as exc:
            raise HTTPException(403, str(exc)) from exc
        new_version, state = await _apply(
            ctx, node_id, delta, expect_version=version - 1,
        )
        ctx.broadcast.publish(topic("plane", node_id), {
            "type": "delta", "node_id": node_id, "version": new_version,
            "state": state, "delta": delta,
            "operator": body.get("operator", ""),
            "timestamp": body.get("timestamp", ""), "sig": body.get("sig", ""),
        })
        return {"stopped": [node_id], "count": 1, "mode": "root_command"}
    # Build the parent map from every node's stored desired state, then BFS down.
    parents: dict[str, str] = {}
    for nid in await ctx.store.list_nodes():
        current = await ctx.store.get_desired(nid)
        parent = (current[1].get("parent") if current else None)
        if isinstance(parent, str) and parent:
            parents[nid] = parent
    subtree = [node_id]
    frontier = {node_id}
    while frontier:
        children = {n for n, p in parents.items() if p in frontier and n not in subtree}
        subtree.extend(sorted(children))
        frontier = children
    stopped = []
    for nid in subtree:
        new_version, state = await _apply(ctx, nid, {"stopped": True})
        ctx.broadcast.publish(topic("plane", nid), {
            "type": "delta", "node_id": nid, "version": new_version,
            "state": state, "delta": {"stopped": True},
            "operator": "op_ui", "timestamp": "", "sig": "",
        })
        stopped.append(nid)
    return {"stopped": stopped, "count": len(stopped), "mode": "bfs_fallback"}


@router.get("/{node_id}/desired")
async def desired_stream(node_id: str, request: Request) -> EventSourceResponse:
    ctx = _ctx(request)
    # The response body is iterated after this handler returns, so the topic
    # binds the tenant NOW rather than relying on the ambient one later.
    node_topic = topic("plane", node_id, current_org_id())
    queue = ctx.broadcast.subscribe(node_topic)

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
            ctx.broadcast.unsubscribe(node_topic, queue)

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
    # Same ceiling as /v1/ingest: the batch is held, parsed and folded in
    # memory, so its size is a resource the caller controls.
    lines: list[dict[str, Any]] = check_batch_size(body.get("events", []))
    await ctx.store.upsert_run(run_id, node_id, body.get("scenario", "live"), now())
    # Delivery is at-least-once: a batch is resent whenever its ack is lost, so
    # applying its effects again would overwrite the node's reported state with a
    # level it had already left — a node degraded to LOCKED read NORMAL in the
    # console, which is the plane's one job told backwards. The Idempotency-Key
    # is what says "already accepted", and it is the ONLY thing that says it.
    # Whether rows were written does not: an adapter that restarts numbers its
    # events from zero again on the same keepalive run, so a genuinely new report
    # can collide with the previous process's rows and store nothing. Gating on
    # that would make a restarted node go permanently dark.
    result = await ctx.store.ingest_events(run_id, node_id, lines, idempotency_key)
    if not result.replayed:
        await _fold_reported(ctx, node_id, run_id, lines)
    # A node that dials in is a node the customer is running today. Recorded on
    # every delivery, replays included: the record is per (node, day) and a
    # replay means the day was already noted, so noting it again costs one cache
    # hit and gating it on `replayed` would lose the day of a node whose only
    # successful delivery that day was a resend.
    await _note_active(ctx, node_id)
    # Each stored event carries the id a reconnecting subscriber resumes from.
    for event_id, line in result.rows:
        ctx.broadcast.publish(
            topic("run", run_id),
            {"type": "event", "id": event_id, "line": line},
        )
    return {"stored": len(result.rows)}


async def _note_active(ctx: Any, node_id: str) -> None:  # noqa: ANN401
    """Record that this node reported today, for the governed-node meter.

    `ctx.active_today` maps (org, node) to the UTC day already written by THIS
    deployment. A heartbeat every ten seconds must not be a write every ten
    seconds; the row is idempotent by primary key anyway, so the cache saves a
    round trip and never decides correctness — a restart simply re-records
    today, and a stale entry costs at most one missing write for a day that is
    already recorded.

    It lives on app.state rather than in a module global because a module
    global outlives the database it was describing: two apps in one process
    (every test file here, and any embedding) would share one cache over two
    stores, and the second would silently record nothing.
    """
    org = current_org_id()
    day = today()
    if ctx.active_today.get((org, node_id)) == day:
        return
    await ctx.store.record_node_activity(node_id, day)
    ctx.active_today[(org, node_id)] = day


async def _fold_reported(
    ctx: Any,  # noqa: ANN401 - app.state is dynamic
    node_id: str,
    run_id: str,
    lines: list[dict[str, Any]],
) -> None:
    """Fold a batch's heartbeats into the node's reported state, once.

    Only the last heartbeat survives in the store — reported state is LWW — so
    the batch is walked to find it and written once, rather than read-and-written
    per line. The walk still compares each step, because a batch that climbs
    NORMAL -> LOCKED -> NORMAL really did reach LOCKED, and the operator is
    entitled to be told even though nothing in the store will remember it.
    """
    heartbeats = [ln for ln in lines if ln.get("kind") == "heartbeat"]
    if any(ln.get("kind") == "operator_intervention" for ln in lines):
        await ctx.store.mark_intervened(run_id)
    if not heartbeats:
        return

    prior = await ctx.store.get_reported(node_id)          # one read
    level = prior["level"] if prior else "NORMAL"
    climbs: list[tuple[str, str]] = []
    for line in heartbeats:
        reached = str((line.get("payload") or {}).get("level", "NORMAL"))
        if _LEVELS.get(reached, 0) > _LEVELS.get(level, 0):
            climbs.append((level, reached))
        level = reached

    last = heartbeats[-1].get("payload") or {}
    await ctx.store.upsert_reported(                        # one write
        node_id,
        applied_version=int(last.get("applied_version", 0)),
        level=str(last.get("level", "NORMAL")),
        budget_remaining=last.get("budget_remaining"),
        ts=now(),
    )
    # Broadcast what was STORED, so a live panel and a reload agree.
    ctx.broadcast.publish(
        topic("plane", node_id),
        {"type": "reported", "node_id": node_id, "reported": last},
    )
    # Upward degradation transition — spec §16 trigger.
    notifier = getattr(ctx, "notifier", None)
    if notifier is None:
        return
    for from_level, to_level in climbs:
        await notifier.emit(
            "level_transition_up", node_id,
            {"from": from_level, "to": to_level,
             "permalink": f"/v1/plane/nodes#{node_id}"},
        )


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
    appended = await ctx.store.append_fact(node_id, fact, now())
    if not appended:
        raise HTTPException(409, "fact_id already exists (append-only)")
    # An operator attestation is an append-only node over the branch it covers
    # (spec 8.1.1) — mirror it into THIS TENANT's taint graph so the graph's
    # attestation surface and the fact log stay one story. Not behind a
    # getattr() default: a missing registry is a wiring bug, and silently
    # skipping the mirror is how an attestation stops reaching the graph
    # without anything failing.
    if fact.get("fact_type") == "operator_attestation":
        import json as _json
        await ctx.graphs.current().append_attestation(_json.dumps(fact))
    ctx.broadcast.publish(
        topic("plane", node_id),
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


# Verdict constants mirrored from axor-probe (the backend never imports it —
# the payload shape is the whole contract, same posture as everywhere else).
_PROBE_VERDICTS = frozenset({
    "CONSISTENT", "DRIFT_DETECTED", "INCONCLUSIVE", "CONSISTENCY_ANOMALY",
})
_FAMILY_STATES = frozenset({"clean", "escaped", "unprobed"})


@router.post("/{node_id}/probe-report", status_code=201)
async def post_probe_report(node_id: str, body: dict, request: Request) -> dict:
    """Ingest one behavioral health check (ui-spec 8.2).

    The node runs the battery and posts the result out-dial, exactly like
    telemetry: the plane never reaches into a customer runtime to invoke their
    agent, and a health check is no exception. The body is axor-probe's
    ``integration.plane.health_payload``.

    This is drift, and it stays drift. It is stored apart from the eval corpus
    and must never be folded into Scenario Delta or a Core score — a drift-red
    agent with a green integrity score is a legitimate, informative combination
    (ui-spec 8.2).
    """
    ctx = _ctx(request)
    verdict = body.get("overall_verdict")
    if verdict not in _PROBE_VERDICTS:
        raise HTTPException(
            400, f"overall_verdict must be one of {sorted(_PROBE_VERDICTS)}"
        )
    families = body.get("families", [])
    if not isinstance(families, list):
        raise HTTPException(400, "`families` must be a list")
    for fam in families:
        if not isinstance(fam, dict) or fam.get("state") not in _FAMILY_STATES:
            raise HTTPException(
                400, f"each family needs a state in {sorted(_FAMILY_STATES)}"
            )
        # `family` is validated because it is READ below, when a DRIFT_DETECTED
        # report names the escaped families in its notification. Checking only
        # `state` left the name unchecked, so a report without one raised a
        # KeyError — a 500 on the route a node posts its own health to, which
        # axor-wrap treats as a programming error and re-raises. The battery
        # crashed instead of the report being rejected.
        if not isinstance(fam.get("family"), str) or not fam["family"]:
            raise HTTPException(400, "each family needs a non-empty `family` name")
    report_id = await ctx.store.add_probe_report(node_id, body, now())
    ctx.broadcast.publish(
        topic("plane", node_id),
        {"type": "probe_report", "node_id": node_id, "report": body},
    )
    notifier = getattr(ctx, "notifier", None)
    if notifier is not None and verdict == "DRIFT_DETECTED":
        await notifier.emit(
            "behavioral_drift", node_id,
            {"escape_count": int(body.get("escape_count", 0)),
             "probes_sent": int(body.get("probes_sent", 0)),
             "families": [f["family"] for f in families
                          if f.get("state") == "escaped"],
             "permalink": f"/v1/plane/nodes#{node_id}"},
        )
    return {"stored": True, "id": report_id}


@router.get("/{node_id}/probe-report")
async def get_probe_report(node_id: str, request: Request) -> dict:
    """The last health check plus the check series behind it.

    `latest` is null when the node has never posted one — the panel renders
    that as "no check yet", which is not the same as a healthy agent. `history`
    is oldest-first; the drift sparkline only appears once it holds ≥2 entries
    (ui-spec 8.2).
    """
    ctx = _ctx(request)
    return {
        "latest": await ctx.store.latest_probe_report(node_id),
        "history": await ctx.store.probe_report_history(node_id),
    }


@router.get("/topology")
async def topology(request: Request) -> dict:
    """The tree as a graph — derived ONLY from traced events (node_spawned /
    message_sent / message_received), never from a node self-reporting its
    parent (spec v2 Ch.4 §6). Edge kinds: delegation | lateral | peer. Nodes
    only ever seen as a peer-edge target are foreign — opaque, no posture."""
    ctx = _ctx(request)
    lines = await ctx.store.topology_events()
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}

    def touch(nid: str, kind: str = "self") -> None:
        if not nid:
            return
        cur = nodes.setdefault(nid, {"node_id": nid, "kind": kind})
        if cur["kind"] == "peer" and kind == "self":
            cur["kind"] = "self"  # locally-traced identity wins over peer sighting

    for line in lines:
        p = line.get("payload") or {}
        kind = line["kind"]
        if kind == "node_spawned":
            parent = p.get("parent_id") or line["node_id"]
            child = p.get("child_id", "")
            touch(parent)
            touch(child)
            key = (parent, child, "delegation")
            e = edges.setdefault(key, {
                "from": parent, "to": child, "kind": "delegation",
                "messages": 0, "denied": 0, "last_gate": None,
            })
            e["spawned"] = True
        elif kind in ("message_sent", "message_received"):
            edge_kind = p.get("edge_kind", "lateral")
            if kind == "message_sent":
                frm, to = line["node_id"], p.get("to", "")
                touch(frm)
                touch(to, "peer" if edge_kind == "peer" else "self")
            else:
                frm, to = p.get("from", ""), line["node_id"]
                touch(frm, "peer" if edge_kind == "peer" else "self")
                touch(to)
            if not frm or not to:
                continue
            e = edges.setdefault((frm, to, edge_kind), {
                "from": frm, "to": to, "kind": edge_kind,
                "messages": 0, "denied": 0, "last_gate": None,
            })
            if kind == "message_sent":
                e["messages"] += 1
                if line.get("verdict") == "deny":
                    e["denied"] += 1
                    e["last_gate"] = line.get("gate")

    # Plane-connected nodes with no traced edges still render (size-1 lists).
    for nid in await ctx.store.list_nodes():
        touch(nid)
    # Two queries for the whole fleet, not two per node.
    desired_by_node = await ctx.store.all_desired()
    reported_by_node = await ctx.store.all_reported()
    for n in nodes.values():
        if n["kind"] != "self":
            continue  # foreign peers are opaque: no posture, no interventions
        current = desired_by_node.get(n["node_id"])
        n["desired"] = (
            {"version": current[0], "state": current[1]} if current else None
        )
        n["reported"] = reported_by_node.get(n["node_id"])
    return {
        "nodes": sorted(nodes.values(), key=lambda n: n["node_id"]),
        "edges": sorted(edges.values(), key=lambda e: (e["from"], e["to"], e["kind"])),
    }


@router.get("/nodes")
async def nodes(request: Request) -> list[dict]:
    """Topology data: desired next to reported — divergence is rendered, not
    hidden (protocol, section 5)."""
    ctx = _ctx(request)
    # Four queries for the whole fleet. Built per node this cost three round
    # trips each — 151 of them for 50 nodes, on a surface the UI polls.
    desired_by_node = await ctx.store.all_desired()
    reported_by_node = await ctx.store.all_reported()
    facts_by_node = await ctx.store.facts_by_node()
    out = []
    for node_id in await ctx.store.list_nodes():
        current = desired_by_node.get(node_id)
        out.append({
            "node_id": node_id,
            "desired": (
                {"version": current[0], "state": current[1]} if current else None
            ),
            "reported": reported_by_node.get(node_id),
            "facts": facts_by_node.get(node_id, []),
        })
    return out


async def _apply(
    ctx: Any,  # noqa: ANN401 - app.state is dynamic
    node_id: str,
    delta: dict[str, Any],
    *,
    expect_version: int | None = None,
) -> tuple[int, dict[str, Any]]:
    """Write a desired-state delta, turning the store's two concurrency
    outcomes into the answers an operator can act on.

    Both are 409, and both mean the same thing to the caller — the command was
    NOT applied, re-read the version and send it again. They are separate types
    because only one of them is safe to retry inside the store.
    """
    try:
        return await ctx.store.bump_desired(
            node_id, delta, expect_version=expect_version,
        )
    except StaleVersion as exc:
        raise HTTPException(409, str(exc)) from exc
    except ConcurrentUpdate as exc:
        raise HTTPException(409, str(exc)) from exc


def _json(value: dict) -> str:
    return json.dumps(value, sort_keys=True)
