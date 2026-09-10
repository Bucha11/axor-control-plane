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
from collections import OrderedDict
from typing import Any

from fastapi import HTTPException


def _ceiling(name: str, default: int) -> int:
    """A positive integer from the environment, or a message that names it.

    These are read at IMPORT time, so a bare ``int(os.environ[...])`` meant a
    typo in one of them stopped the whole backend from importing, with a
    ValueError that quoted the value and not the variable it came from.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{name}={raw!r} is not a whole number") from None
    if value < 1:
        raise ValueError(f"{name}={raw!r} must be >= 1")
    return value


MAX_EVENTS_PER_BATCH = _ceiling("AXOR_MAX_EVENTS_PER_BATCH", 10000)
# Cached causal subgraphs held per process (app._subgraph_cache).
SUBGRAPH_CACHE_MAX = _ceiling("AXOR_SUBGRAPH_CACHE_MAX", 512)
# Regression pins one uploaded Lab package may carry. Each one is content-hashed,
# converted to kernel events and REPLAYED before the request answers, so the
# per-pin cost is real work and the count is caller-chosen. A genuine bundle pins
# tens of cases, not thousands.
MAX_PINS_PER_PACKAGE = _ceiling("AXOR_MAX_PINS_PER_PACKAGE", 500)

# k-hop bounds for the per-run provenance walk. Both are resource bounds on a
# caller-chosen number: without them `?k=` and `?limit=` sized the walk from the
# query string. 30 hops is far past any real value chain in one run — the walk
# stops early when the frontier empties, so the cap only ever binds a request
# that was asking for the whole run anyway.
MAX_KHOP_K = _ceiling("AXOR_MAX_KHOP_K", 30)
MAX_KHOP_LIMIT = _ceiling("AXOR_MAX_KHOP_LIMIT", 1000)


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


class SubgraphCache:
    """Derive-on-open cache for causal subgraphs (decision v2-12).

    A subgraph costs one pure kernel walk to rebuild, so this is a convenience,
    not a source of truth — which is why eviction is plain insertion order.

    Two properties it must have, both learned the hard way:

    - **Keyed by tenant.** Two tenants legitimately hold the same run id (a Lab
      pin is the deterministic ``lab:{trace_id}``), so an org-blind key served
      one tenant's causal subgraph to the other.
    - **Bounded, and dropped when its run grows.** An unbounded dict on a
      public route is a memory leak anyone can drive. And "traces are
      append-only" does not mean a run is immutable: ``POST /v1/ingest/{run_id}``
      and a Lab re-deploy both append to an existing run, after which every
      cached subgraph for it is missing the new derivations. The writers call
      :meth:`drop_run`.
    """

    def __init__(self, maxsize: int = SUBGRAPH_CACHE_MAX) -> None:
        self._maxsize = maxsize
        self._entries: OrderedDict[tuple, dict] = OrderedDict()

    def __len__(self) -> int:
        return len(self._entries)

    def get(self, org: str, run_id: str, anchor_node: str, anchor_seq: int) -> dict | None:
        return self._entries.get((org, run_id, anchor_node, anchor_seq))

    def put(
        self, org: str, run_id: str, anchor_node: str, anchor_seq: int, value: dict
    ) -> dict:
        self._entries[(org, run_id, anchor_node, anchor_seq)] = value
        while len(self._entries) > self._maxsize:
            self._entries.popitem(last=False)
        return value

    def drop_run(self, org: str, run_id: str) -> int:
        """Forget every anchor of one run, because its events changed."""
        stale = [k for k in self._entries if k[0] == org and k[1] == run_id]
        for key in stale:
            del self._entries[key]
        return len(stale)
