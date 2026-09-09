"""Node-stale monitor (spec §16 trigger: node_stale after 3T silence).

Webhook events cover level transitions and evidence runs, but a node that stops
heartbeating emits nothing — the ABSENCE of a signal is itself the signal. So a
background pass scans reported timestamps and fires node_stale when a node has
been silent past the stale window (default 3T, T = the heartbeat period).

Edge-triggered: a node fires node_stale once when it crosses into stale, and
becomes eligible to fire again only after it heartbeats and goes stale anew — so
the on-call is paged on the transition, not every sweep.

The edge lives in the node's row (`reported_state.stale_notified`), not in this
process. It used to be a set in memory, which made a backend restart a reason to
page: nothing deletes a reported row, so every node that had ever gone quiet —
decommissioned ones included — fired again on the first sweep after every
restart. A restart is not a heartbeat. Keeping the flag beside the heartbeat
that clears it also means the two cannot disagree, and a node that went silent
WHILE the backend was down is still paged, exactly once, when it comes back up.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from axor_backend.tenancy import topic

log = logging.getLogger("axor.backend.monitor")

HEARTBEAT_PERIOD = 10.0          # protocol §9: static T
STALE_AFTER = 3 * HEARTBEAT_PERIOD  # 3T


def _parse_ts(ts: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def stale_sweep(
    store: Any,  # noqa: ANN401 - the backend Store
    notifier: Any,  # noqa: ANN401 - the Notifier
    broadcast: Any,  # noqa: ANN401 - the Broadcast bus
    stale_after: float,
    now: datetime,
) -> int:
    """One pass. Emits node_stale for nodes newly past the stale window.

    "Newly" is decided by the row: `mark_stale_notified` only writes when the
    flag is still unset, so claiming it and paging are the same step, and the
    re-arm is the heartbeat's own write (`upsert_reported` clears it). There is
    no per-process memory of who has been paged — that is what made a restart
    page the whole graveyard.

    Returns the number of node_stale notifications emitted this pass.
    """
    emitted = 0
    for row in await store.list_reported():
        node_id = row["node_id"]
        ts = _parse_ts(row["updated_ts"])
        if ts is None:
            # The one input this monitor has is unreadable, so it cannot say
            # whether the node is silent — and staying quiet about that is the
            # failure mode this whole file exists to prevent. `upsert_reported`
            # is the only writer and stamps `clock.now()`, so a value that does
            # not parse means the row was written by something else.
            log.warning(
                "node %s: last heartbeat timestamp %r does not parse — this node "
                "is NOT being watched for silence",
                node_id, row["updated_ts"],
            )
            continue
        silent = (now - ts).total_seconds()
        if silent < stale_after or row.get("stale_notified") is not None:
            continue
        if not await store.mark_stale_notified(node_id, now.isoformat()):
            continue  # someone else claimed this silence between the read and here
        broadcast.publish(
            topic("plane", node_id),
            {"type": "node_stale", "node_id": node_id,
             "silent_seconds": silent},
        )
        await notifier.emit(
            "node_stale", node_id,
            {"silent_seconds": round(silent, 1),
             "last_level": row["level"],
             "permalink": f"/v1/plane/nodes#{node_id}"},
        )
        emitted += 1
    return emitted


async def stale_monitor(
    store: Any,  # noqa: ANN401
    notifier: Any,  # noqa: ANN401
    broadcast: Any,  # noqa: ANN401
    interval: float = HEARTBEAT_PERIOD,
    stale_after: float = STALE_AFTER,
) -> None:
    """Sweep forever, `interval` apart, once per tenant. Cancelled at shutdown.

    Per tenant because this loop is a background task and therefore carries no
    request: the ambient organization is the public one, so a single sweep only
    ever saw the public tenant's nodes. An identity organization's node could go
    silent forever without anyone being paged — a failure of the one trigger
    whose whole purpose is to notice silence.

    Nothing about who has been paged is carried between sweeps here: the flag is
    a column on the node's own row, so it is already scoped to the tenant that
    owns the node and two tenants may run a node of the same name.
    """
    from axor_backend.tenancy import PUBLIC_ORG, set_current_org

    while True:
        await asyncio.sleep(interval)
        try:
            now = datetime.now(UTC)
            for org in await store.list_orgs():
                set_current_org(org)
                await stale_sweep(store, notifier, broadcast, stale_after, now)
        except Exception as exc:  # noqa: BLE001 - a sweep error must not kill the loop
            log.warning("stale sweep failed: %s", exc)
        finally:
            set_current_org(PUBLIC_ORG)


def spawn_stale_monitor(app: Any) -> asyncio.Task[None]:  # noqa: ANN401
    """Start the monitor as a lifespan-scoped task. Reads AXOR_STALE_AFTER /
    AXOR_STALE_SWEEP_INTERVAL (seconds) for deployments that want a different
    cadence; defaults are 3T / T."""
    import os

    def seconds(name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            value = float(raw)
        except ValueError:
            raise ValueError(
                f"{name}={raw!r} is not a number of seconds"
            ) from None
        if value <= 0:
            raise ValueError(f"{name}={raw!r} must be > 0")
        return value

    stale_after = seconds("AXOR_STALE_AFTER", STALE_AFTER)
    interval = seconds("AXOR_STALE_SWEEP_INTERVAL", HEARTBEAT_PERIOD)
    return asyncio.create_task(
        stale_monitor(app.state.store, app.state.notifier,
                      app.state.broadcast, interval, stale_after)
    )


@contextlib.asynccontextmanager
async def running_stale_monitor(app: Any):  # noqa: ANN201, ANN401
    """Context manager wrapping spawn + clean cancellation, for the lifespan."""
    task = spawn_stale_monitor(app)
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
