"""Running synchronous work without holding the event loop.

The backend is single-instance by design (docs/ops-limits.md), so there is no
second worker to take over while one request computes. Kernel folds, trace
parsing and package conversion are all pure CPU: called straight from an
``async def`` they hold the loop for their whole duration, and everything the
process owes in that window — the plane's heartbeats, an ingest, the liveness
probe docker-compose polls with ``timeout: 5s`` and gates two services on —
waits.

Measured as the overshoot of 20 ms timers that could not fire, on one request:

    GET /v1/replay (60 000 events)      worst overshoot 1141 ms
    GET /v1/runs/{id}/lab-package (40k) worst overshoot 1181 ms
    POST /v1/runs/{id}/influence (401)  worst overshoot  904 ms

``asyncio.to_thread`` does not make the work cheaper, and under the GIL it does
not make it parallel. What it changes is that the interpreter switches out of
the running thread, so a long fold stops meaning a dead process.

Every route that calls the kernel goes through here. The rule is worth stating
plainly because it is easy to half-apply: the first pass of this fix handed off
``replay`` and left ``events_for`` — which parses every line of the run — on the
loop, and a 60 000-event replay still starved it for 1.1 s.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable


async def off_loop[T](fn: Callable[..., T], /, *args: object) -> T:
    """Run one synchronous computation off the event loop."""
    return await asyncio.to_thread(fn, *args)
