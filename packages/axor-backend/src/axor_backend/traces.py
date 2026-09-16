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

from axor_core.kernel.events import Event
from fastapi import HTTPException

from axor_backend.offload import off_loop
from axor_backend.replay_api import parse_trace
from axor_backend.storage import Store

# The store used to hand these back as strings and every caller parsed them
# again; the note here said that was 8% of a 200 000-event read and not worth
# changing four call sites for. Against the right denominator it is not 8%: of
# the 1.93 s a 60 000-event read spends in Python, 1.17 s was the dump/parse
# round trip and 0.76 s the query — and all of it was held on the event loop.
# `run_events` returns the dicts the column holds now, and the one serialisation
# the KERNEL still requires happens below, inside the thread.
#
# All of this runs OFF the event loop (`offload.off_loop`). Worth stating because
# it is easy to half-apply: the first pass handed off `replay` and left the parse
# behind, and a 60 000-event `GET /v1/replay` still starved the loop for 1141 ms.


def _kernel_trace(lines: list[dict], run_id: str, required: bool) -> list[Event]:  # noqa: FBT001
    """Filter to kernel-schema lines and parse them. Pure, so it can be handed
    to a thread; the 4xx it raises is re-raised on the caller's side unchanged.

    The store hands back the dicts the column holds. `event_from_json_line`
    takes a string and Rule 0 says the platform imports the kernel's reader
    rather than rebuilding it, so the serialisation happens here — in the
    thread, not on the loop, and once instead of the dump/parse/parse the store
    used to do on the way out.
    """
    kernel_lines = [json.dumps(ln) for ln in lines if ln.get("schema_version")]
    if not kernel_lines:
        if not required:
            return []
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


async def events_for(store: Store, run_id: str) -> list[Event]:
    """The run's kernel events, or an HTTPException saying why there are none."""
    lines = await store.run_events(run_id)
    if not lines:
        raise HTTPException(404, f"no events for run {run_id}")
    return await off_loop(_kernel_trace, lines, run_id, True)


async def kernel_events_or_empty(store: Store, run_id: str) -> list[Event]:
    """The run's kernel events, with "there are none" as an answer, not a 4xx.

    :func:`events_for` serves replay, where a run with no kernel trace is a
    request that cannot be honoured. The plane's coverage view is the other
    case: a node whose telemetry carries no kernel schema at all — a proxy, or
    any client that is not axor-wrap's plane client, which stamps
    ``schema_version`` on every line it sends — has recorded no facts, and "no
    facts, level NORMAL, nothing to attest" is the correct answer about it. A
    422 there would put an error in the panel where the truth is "nothing".

    A run whose kernel lines are there but do not parse is still a 422: that is
    a broken trace, and answering "no facts" for it would report a node as
    healthy because its trace could not be read.
    """
    return await off_loop(
        _kernel_trace, await store.run_events(run_id), run_id, False)
