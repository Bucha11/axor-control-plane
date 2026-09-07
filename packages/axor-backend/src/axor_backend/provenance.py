"""Value provenance inside ONE run, derived on demand from that run's events.

There is no provenance store, and that is the point.

A value ref is minted by the runtime's per-trace ledger (``axor_wrap.trace``),
whose counter starts at zero on every build: the first external input of every
run is ``v_ext_1``, the second minted model value of every run is ``v_model_2``.
Refs are unique WITHIN a run and collide ACROSS runs by construction.

A stored graph keyed on those refs is therefore not an index of the fleet's
provenance, it is a merge of unrelated values — Monday's ``v_ext_1`` and
Friday's ``v_ext_1`` are one node, and a caller asking for the neighbourhood of
one is answered with the other's edges. Persisting it (a per-tenant embedded DB
file, rehydrated from the event log at boot) only bought durability for an
answer that was wrong the moment a second run existed.

So the run is the unit. Provenance is recomputed from that run's own events on
request: the same walk the store used to fold at ingest, over the same rows,
scoped to where the refs mean something. One pass over one run's events, the
events table stays the only copy, and there is nothing to rehydrate.

The derivation is the one the replay kernel folds (see
``replay._derive_driving_root``): a TOOL_RESULT's produced value derives from
the input refs of the TOOL_CALL it answers, paired PER NODE. A kernel event
carries no call id — only ``seq``, ``node_id`` and the payload — so the pairing
is node plus order, which is exactly the ordering the kernel guarantees, and a
run can carry several nodes (migration 0004).
"""
from __future__ import annotations

from typing import Any

from axor_core.kernel.events import EventKind


def _payload(event: Any) -> dict:  # noqa: ANN401 - kernel Event or raw JSON line
    payload = getattr(event, "payload", None)
    return payload if isinstance(payload, dict) else {}


def derivation_edges(events: list[Any]) -> list[dict[str, str]]:
    """``src -> dst`` value derivations recorded in one run, in event order.

    Deduplicated: a run that calls the same tool with the same inputs twice
    records the same edge twice, and an edge is a set member, not a count.
    """
    pending: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    edges: list[dict[str, str]] = []
    for event in events:
        kind = getattr(event, "kind", None)
        payload = _payload(event)
        node = str(getattr(event, "node_id", "") or "")
        if kind == EventKind.TOOL_CALL:
            arg_refs = payload.get("arg_refs") or {}
            pending[node] = [str(r) for r in arg_refs.values() if r]
        elif kind == EventKind.TOOL_RESULT:
            dst = payload.get("value_ref") or getattr(event, "causal_root", None)
            if dst:
                for src in pending.get(node, ()):
                    key = (src, str(dst))
                    if key in seen:
                        continue
                    seen.add(key)
                    edges.append({"src": src, "dst": str(dst)})
            pending.pop(node, None)
    return edges


def adjacency(edges: list[dict[str, str]]) -> dict[str, set[str]]:
    """Undirected adjacency: a taint graph is explored up (where did this come
    from) and down (what did this reach), so both directions are walkable."""
    adj: dict[str, set[str]] = {}
    for edge in edges:
        adj.setdefault(edge["src"], set()).add(edge["dst"])
        adj.setdefault(edge["dst"], set()).add(edge["src"])
    return adj


def khop(events: list[Any], focus: str, k: int, limit: int) -> dict[str, object]:
    """Undirected k-hop neighbourhood of ``focus`` within one run (decision 6),
    expand-on-click, capped at ``limit`` nodes."""
    edges = derivation_edges(events)
    adj = adjacency(edges)
    seen = {focus}
    frontier = {focus}
    for _ in range(max(int(k), 0)):
        nxt: set[str] = set()
        for node in frontier:
            nxt |= adj.get(node, set())
        nxt -= seen
        if not nxt:
            break
        for node in sorted(nxt):
            if len(seen) >= int(limit):
                break
            seen.add(node)
        frontier = nxt & seen
    return {
        "focus": focus,
        "nodes": sorted(seen),
        "edges": [e for e in edges if e["src"] in seen and e["dst"] in seen],
    }
