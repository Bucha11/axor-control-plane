"""The door events come in through: the ceilings, and whether a line is one.

Ingest and plane telemetry accept a caller-supplied list of events, and the
whole batch is held in memory, parsed and folded before anything is stored — so
"however many you send" was a resource the caller controlled entirely, on routes
reachable with the `ingest` scope. Replay reads a whole run back the same way,
which is why there is a ceiling on the RUN and not only on the request: the
per-request one never bounded what a read costs.

The ceilings are deliberately generous: a real multi-node trace is thousands of
events, not millions. They exist so an accident or an abusive client fails with
a 413 that says what to do, instead of an OOM that takes the process down.

Shape belongs here for the same reason size does — this is the only door, and
both were the caller's to get wrong. What the door refuses, the log never holds;
what it lets through, `traces.py` can still only apologise for.

Lives in its own module rather than in ``app`` because the plane router needs it
too, and ``app`` imports the router — the other direction would be a cycle. The
ceilings are therefore read here and not in ``config``; they go through the same
reader (``axor_backend.env``), and ``test_env_surface`` holds them to the same
documentation rule as everything in ``AppConfig``.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from typing import Any

from axor_core.kernel.events import event_from_json_line
from fastapi import HTTPException

from axor_backend import env

MAX_EVENTS_PER_BATCH = env.integer("AXOR_MAX_EVENTS_PER_BATCH", 10000, minimum=1)
# The whole RUN, across every batch that built it. The per-batch ceiling bounds
# one request; it never bounded the run, so 20 individually legal batches of
# 10 000 made a trace that every read materialises whole — `GET /v1/replay/{run}`
# answering 200 after 9 s with a 71 MB body and 545 MiB of resident memory, on a
# route that needs only `read` scope, in a process the architecture note says to
# scale up rather than out. The k-hop caps below have the same shape of hole
# without this: they bound the walk, not the load underneath it, so a 2-hop
# question about one value spent 5 s reading 200 000 events to return 40 bytes.
#
# It is enforced on the WRITE, not the read. A ceiling on the read would make an
# already-recorded trace permanently unreadable, which is worse than slow — the
# audit log stops being auditable. Refusing the batch that would cross it tells
# the client, who can start a new run id, while it can still act.
MAX_EVENTS_PER_RUN = env.integer("AXOR_MAX_EVENTS_PER_RUN", 250000, minimum=1)
# Cached causal subgraphs held per process (app._subgraph_cache).
SUBGRAPH_CACHE_MAX = env.integer("AXOR_SUBGRAPH_CACHE_MAX", 512, minimum=1)
# Regression pins one uploaded Lab package may carry. Each one is content-hashed,
# converted to kernel events and REPLAYED before the request answers, so the
# per-pin cost is real work and the count is caller-chosen. A genuine bundle pins
# tens of cases, not thousands.
MAX_PINS_PER_PACKAGE = env.integer("AXOR_MAX_PINS_PER_PACKAGE", 500, minimum=1)

# k-hop bounds for the per-run provenance walk. Both are resource bounds on a
# caller-chosen number: without them `?k=` and `?limit=` sized the walk from the
# query string. 30 hops is far past any real value chain in one run — the walk
# stops early when the frontier empties, so the cap only ever binds a request
# that was asking for the whole run anyway.
MAX_KHOP_K = env.integer("AXOR_MAX_KHOP_K", 30, minimum=1)
MAX_KHOP_LIMIT = env.integer("AXOR_MAX_KHOP_LIMIT", 1000, minimum=1)


def check_batch(events: Any, what: str = "events") -> list[dict[str, Any]]:  # noqa: ANN401
    """Validate a caller-supplied event batch, or raise a 4xx that says why.

    Size AND shape, because both were the caller's to get wrong and neither was
    answered honestly. A batch carrying a bare string, or an event missing
    ``seq`` or ``kind``, reached the INSERT and came back as a bare
    ``500 {"error": "internal"}`` — the caller's error reported as ours, with
    nothing they could act on. And a line whose ``schema_version`` named a major
    the kernel cannot read was stored with ``202``, after which every read of
    that run answered 422 forever (replay, containment, provenance, coverage,
    the corpus) with no way to remove it: the 4xx landed on the operator instead
    of the client, at a time nobody could fix it.

    The oracle for "is this a kernel event" is the kernel's own reader, never a
    second schema check living here (rule 0). It costs ~86 ms for a batch at the
    10 000 ceiling against ~162 ms for the write it guards, and microseconds for
    the tens-to-hundreds a real trace sends. Paying it at the door is what keeps
    the event log — the only copy — readable by the thing that has to read it.
    """
    if not isinstance(events, list):
        raise HTTPException(400, f"`{what}` must be a list")
    if len(events) > MAX_EVENTS_PER_BATCH:
        raise HTTPException(
            413,
            f"{len(events)} {what} exceeds the per-request ceiling of "
            f"{MAX_EVENTS_PER_BATCH} (AXOR_MAX_EVENTS_PER_BATCH); split the "
            f"batch — ingest is idempotent per Idempotency-Key.",
        )
    for index, line in enumerate(events):
        _check_line(index, line, what)
    return events


def _check_line(index: int, line: Any, what: str) -> None:  # noqa: ANN401
    """One event's shape. A 422 naming the index, or nothing."""
    where = f"`{what}[{index}]`"
    if not isinstance(line, dict):
        raise HTTPException(422, f"{where} must be an object, got {type(line).__name__}")
    # The storage coordinate. Every line needs it, kernel-schema or plane
    # telemetry, because (node_id, seq) is what dedupe and resume are keyed on.
    if "seq" not in line:
        raise HTTPException(422, f"{where} has no `seq`")
    try:
        int(line["seq"])
    except (TypeError, ValueError):
        raise HTTPException(
            422, f"{where}.seq must be an integer, got {line['seq']!r}"
        ) from None
    if not isinstance(line.get("kind"), str) or not line["kind"]:
        raise HTTPException(422, f"{where} has no `kind`")
    if not isinstance(line.get("node_id", ""), str):
        raise HTTPException(422, f"{where}.node_id must be a string")
    # No `schema_version` means plane telemetry — a heartbeat from a proxy, or
    # any client that is not axor-wrap's plane client. It is stored and read back
    # as telemetry (traces.events_for drops it), so the kernel never sees it and
    # must not be asked to judge it.
    if "schema_version" not in line:
        return
    try:
        event_from_json_line(json.dumps(line))
    except Exception as exc:  # noqa: BLE001 - whatever the kernel refuses, say so
        raise HTTPException(
            422,
            f"{where} claims kernel schema but the kernel cannot read it: {exc}. "
            "Send it without `schema_version` if it is plane telemetry.",
        ) from exc


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
