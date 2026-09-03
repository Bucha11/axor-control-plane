"""Per-request ceilings.

Ingest and plane telemetry accept a caller-supplied list of events, and the
whole batch is held in memory, parsed and folded before anything is stored — so
"however many you send" was a resource the caller controlled entirely, on routes
reachable with the `ingest` scope. Replay reads a whole run back the same way.

The ceilings are deliberately generous: a real multi-node trace is thousands of
events, not millions. They exist so an accident or an abusive client fails with
a 413 that says what to do, instead of an OOM that takes the process down.

Lives in its own module rather than in ``app`` because the plane router needs it
too, and ``app`` imports the router — the other direction would be a cycle.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

MAX_EVENTS_PER_BATCH = int(os.environ.get("AXOR_MAX_EVENTS_PER_BATCH", "10000"))
# Cached causal subgraphs held per process (app._subgraph_cache).
SUBGRAPH_CACHE_MAX = int(os.environ.get("AXOR_SUBGRAPH_CACHE_MAX", "512"))


def check_batch_size(events: Any, what: str = "events") -> list:  # noqa: ANN401
    """Validate a caller-supplied event batch, or raise a 4xx that says why."""
    if not isinstance(events, list):
        raise HTTPException(400, f"`{what}` must be a list")
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise HTTPException(
            413,
            f"{len(events)} {what} exceeds the per-request ceiling of "
            f"{MAX_EVENTS_PER_BATCH} (AXOR_MAX_EVENTS_PER_BATCH); split the "
            f"batch — ingest is idempotent per Idempotency-Key.",
        )
    return events
