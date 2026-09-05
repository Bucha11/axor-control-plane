"""Reading a stored run back as a kernel trace.

One function, but it guards a real seam: what the store holds under a run id is
not always a replayable trace. A run's lines can mix kernel-schema events with
plane telemetry, and a governed node's keepalive run is heartbeat-only. Replay
is defined over the kernel trace, so the telemetry is dropped and the caller
gets an honest 4xx — rather than the kernel's ``SchemaVersionError`` surfacing
as a 500 on an ordinary request.
"""
from __future__ import annotations

import json

from fastapi import HTTPException

from axor_backend.replay_api import parse_trace
from axor_backend.storage import Store


async def events_for(store: Store, run_id: str) -> list:
    """The run's kernel events, or an HTTPException saying why there are none."""
    lines = await store.run_events(run_id)
    if not lines:
        raise HTTPException(404, f"no events for run {run_id}")
    kernel_lines = [ln for ln in lines if json.loads(ln).get("schema_version")]
    if not kernel_lines:
        raise HTTPException(
            422,
            f"run {run_id} has no kernel-schema events to replay "
            "(plane telemetry only — e.g. heartbeats)",
        )
    try:
        return parse_trace(kernel_lines)
    except Exception as exc:  # kernel parse errors are client data errors here
        raise HTTPException(
            422, f"run {run_id} is not a replayable kernel trace: {exc}"
        ) from exc
