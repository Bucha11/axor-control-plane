"""Node-stale monitor (spec §16 trigger: node_stale after 3T silence).

Webhook events cover level transitions and evidence runs, but a node that stops
heartbeating emits nothing — the ABSENCE of a signal is itself the signal. So a
background pass scans reported timestamps and fires node_stale when a node has
been silent past the stale window (default 3T, T = the heartbeat period).

Edge-triggered: a node fires node_stale once when it crosses into stale, and
becomes eligible to fire again only after it heartbeats and goes stale anew — so
the on-call is paged on the transition, not every sweep.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

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
    already_stale: set[str],
    now: datetime,
) -> int:
    """One pass. Emits node_stale for nodes newly past the stale window; clears
    the flag for nodes that have heartbeated since, so a later silence re-fires.
    Returns the number of node_stale notifications emitted this pass."""
    emitted = 0
    live_nodes: set[str] = set()
    for row in await store.list_reported():
        node_id = row["node_id"]
        live_nodes.add(node_id)
        ts = _parse_ts(row["updated_ts"])
        if ts is None:
            continue
        silent = (now - ts).total_seconds()
        if silent >= stale_after:
            if node_id not in already_stale:
                already_stale.add(node_id)
                broadcast.publish(
                    f"plane:{node_id}",
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
        else:
            already_stale.discard(node_id)  # fresh again → re-arm
    # A node dropped from the store entirely is no longer ours to page on.
    already_stale.intersection_update(live_nodes)
    return emitted


async def stale_monitor(
    store: Any,  # noqa: ANN401
    notifier: Any,  # noqa: ANN401
    broadcast: Any,  # noqa: ANN401
    interval: float = HEARTBEAT_PERIOD,
    stale_after: float = STALE_AFTER,
) -> None:
    """Sweep forever, `interval` apart. Cancelled at app shutdown."""
    already_stale: set[str] = set()
    while True:
        await asyncio.sleep(interval)
        try:
            await stale_sweep(
                store, notifier, broadcast, stale_after,
                already_stale, datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001 - a sweep error must not kill the loop
            log.warning("stale sweep failed: %s", exc)


def spawn_stale_monitor(app: Any) -> asyncio.Task[None]:  # noqa: ANN401
    """Start the monitor as a lifespan-scoped task. Reads AXOR_STALE_AFTER /
    AXOR_STALE_SWEEP_INTERVAL (seconds) for deployments that want a different
    cadence; defaults are 3T / T."""
    import os

    stale_after = float(os.environ.get("AXOR_STALE_AFTER", STALE_AFTER))
    interval = float(os.environ.get("AXOR_STALE_SWEEP_INTERVAL", HEARTBEAT_PERIOD))
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
