"""GraphStore: Sentinel taint/reputation graph behind a Protocol.

Kuzu everywhere (architecture decision): embedded, per-tenant DB file on
hosted (tenant isolation for free), same Cypher-ish queries on both deploys.
Neo4j remains a legacy hosted backend behind this same Protocol until
migrated.

Single-writer answer (bundle loose end 4, verified by test_graph.py): Kuzu
allows one writing process per database; concurrent writes inside one process
are serialized here with an asyncio lock, and the blocking calls run in a
worker thread. That fits the ingest path because ingestion is per-tenant and
this store is instantiated per tenant DB file — writers never cross tenants.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Protocol


async def register_trace_derivations(
    graph: GraphStore, run_id: str, events: list[dict[str, Any]]
) -> int:
    """Fold an ingested trace into the taint graph: a TOOL_RESULT's produced
    value derives from the input refs of the TOOL_CALL it answers. This is the
    same arg_refs → value_ref provenance the replay kernel folds (see
    replay._derive_driving_root), so the graph and the counterfactual agree on
    what flows where. Returns the number of edges registered."""
    pending_inputs: list[str] = []
    registered = 0
    for line in events:
        kind = line.get("kind")
        payload = line.get("payload", line)
        if kind == "tool_call":
            arg_refs = payload.get("arg_refs") or {}
            pending_inputs = [r for r in arg_refs.values() if r]
        elif kind == "tool_result":
            dst = payload.get("value_ref") or line.get("causal_root")
            if dst:
                for src in pending_inputs:
                    await graph.register_derivation(src, dst, run_id)
                    registered += 1
            pending_inputs = []
    return registered


class GraphStore(Protocol):
    # object: nodes+edges payload for UI
    async def khop(self, focus: str, k: int, limit: int) -> dict[str, object]: ...
    async def register_derivation(
        self, src_ref: str, dst_ref: str, run_id: str
    ) -> None: ...
    async def append_attestation(self, fact_json: str) -> None: ...
    async def branch_attestations(self, ref: str) -> list[dict]: ...


class InMemoryGraphStore:
    """Pure-Python GraphStore — the dev/test default, exactly as SQLite is for the
    relational store. No kuzu dependency, so the taint-graph feature works out of
    the box; a hosted deployment swaps in KuzuGraphStore (per-tenant DB file) via
    the same Protocol. State is process-local and not persisted."""

    def __init__(self) -> None:
        # dst_ref → list of (src_ref, run_id) derivations INTO it, and the reverse
        # so k-hop can walk both directions (a taint graph is explored up and down).
        self._edges: list[dict[str, str]] = []
        self._attestations: list[dict] = []
        self._lock = asyncio.Lock()

    async def register_derivation(self, src_ref: str, dst_ref: str, run_id: str) -> None:
        async with self._lock:
            edge = {"src": src_ref, "dst": dst_ref, "run_id": run_id}
            if edge not in self._edges:
                self._edges.append(edge)

    async def khop(self, focus: str, k: int, limit: int) -> dict[str, object]:
        """Undirected k-hop neighbourhood of `focus` (spec decision 6). Explores
        both directions — a value's provenance (upstream) and what it tainted
        (downstream) — expand-on-click, capped at `limit` nodes."""
        k = int(k)
        limit = int(limit)
        adj: dict[str, set[str]] = {}
        for e in self._edges:
            adj.setdefault(e["src"], set()).add(e["dst"])
            adj.setdefault(e["dst"], set()).add(e["src"])
        seen = {focus}
        frontier = {focus}
        for _ in range(max(k, 0)):
            nxt: set[str] = set()
            for node in frontier:
                nxt |= adj.get(node, set())
            nxt -= seen
            if not nxt:
                break
            for node in sorted(nxt):
                if len(seen) >= limit:
                    break
                seen.add(node)
            frontier = nxt & seen
        edges = [
            e for e in self._edges if e["src"] in seen and e["dst"] in seen
        ]
        return {"focus": focus, "nodes": sorted(seen), "edges": edges}

    async def append_attestation(self, fact_json: str) -> None:
        async with self._lock:
            fact = json.loads(fact_json)
            self._attestations.append({
                "fact_id": fact["fact_id"],
                "operator": fact.get("operator") or "",
                "reason": fact.get("reason") or "",
                "revokes": fact.get("revokes") or None,
                "covers": list(fact.get("covers", ())),
            })

    async def branch_attestations(self, ref: str) -> list[dict]:
        return [
            {"fact_id": a["fact_id"], "operator": a["operator"],
             "reason": a["reason"], "revokes": a["revokes"]}
            for a in self._attestations if ref in a["covers"]
        ]


class KuzuGraphStore:
    """One writer per tenant DB file; reads share the same connection."""

    def __init__(self, db_dir: str | Path, tenant: str) -> None:
        import kuzu

        Path(db_dir).mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(Path(db_dir) / f"{tenant}.kuzu"))
        self._conn = kuzu.Connection(self._db)
        self._write_lock = asyncio.Lock()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        for ddl in (
            "CREATE NODE TABLE IF NOT EXISTS Value(ref STRING, PRIMARY KEY(ref))",
            "CREATE NODE TABLE IF NOT EXISTS Attestation("
            "fact_id STRING, operator STRING, reason STRING, sig STRING, "
            "revokes STRING, PRIMARY KEY(fact_id))",
            "CREATE REL TABLE IF NOT EXISTS DERIVES(FROM Value TO Value, run_id STRING)",
            "CREATE REL TABLE IF NOT EXISTS ATTESTS(FROM Attestation TO Value)",
        ):
            self._conn.execute(ddl)

    async def register_derivation(self, src_ref: str, dst_ref: str, run_id: str) -> None:
        async with self._write_lock:
            await asyncio.to_thread(self._register, src_ref, dst_ref, run_id)

    def _register(self, src_ref: str, dst_ref: str, run_id: str) -> None:
        for ref in (src_ref, dst_ref):
            self._conn.execute(
                "MERGE (v:Value {ref: $ref})", parameters={"ref": ref}
            )
        self._conn.execute(
            "MATCH (a:Value {ref: $src}), (b:Value {ref: $dst}) "
            "CREATE (a)-[:DERIVES {run_id: $run_id}]->(b)",
            parameters={"src": src_ref, "dst": dst_ref, "run_id": run_id},
        )

    async def khop(self, focus: str, k: int, limit: int) -> dict[str, object]:
        """k-hop neighborhood of the focus (spec decision 6), expand-on-click."""
        return await asyncio.to_thread(self._khop, focus, int(k), int(limit))

    def _khop(self, focus: str, k: int, limit: int) -> dict[str, object]:
        result = self._conn.execute(
            f"MATCH (a:Value {{ref: $focus}})-[e:DERIVES*1..{k}]-(b:Value) "
            f"RETURN DISTINCT b.ref LIMIT {limit}",
            parameters={"focus": focus},
        )
        nodes = {focus}
        while result.has_next():
            nodes.add(result.get_next()[0])
        edge_result = self._conn.execute(
            "MATCH (a:Value)-[e:DERIVES]->(b:Value) "
            "WHERE list_contains($nodes, a.ref) AND list_contains($nodes, b.ref) "
            "RETURN a.ref, b.ref, e.run_id",
            parameters={"nodes": sorted(nodes)},
        )
        edges = []
        while edge_result.has_next():
            src, dst, run_id = edge_result.get_next()
            edges.append({"src": src, "dst": dst, "run_id": run_id})
        return {"focus": focus, "nodes": sorted(nodes), "edges": edges}

    async def append_attestation(self, fact_json: str) -> None:
        """Attestation is an append-only node linked to the branch it covers
        (spec 8.1.1) — never a mutation of heat."""
        async with self._write_lock:
            await asyncio.to_thread(self._append_attestation, fact_json)

    def _append_attestation(self, fact_json: str) -> None:
        fact = json.loads(fact_json)
        self._conn.execute(
            "CREATE (a:Attestation {fact_id: $fact_id, operator: $operator, "
            "reason: $reason, sig: $sig, revokes: $revokes})",
            parameters={
                "fact_id": fact["fact_id"],
                "operator": fact.get("operator") or "",
                "reason": fact.get("reason") or "",
                "sig": fact.get("sig") or "",
                "revokes": fact.get("revokes") or "",
            },
        )
        for ref in fact.get("covers", ()):
            self._conn.execute(
                "MERGE (v:Value {ref: $ref})", parameters={"ref": ref}
            )
            self._conn.execute(
                "MATCH (a:Attestation {fact_id: $fact_id}), (v:Value {ref: $ref}) "
                "CREATE (a)-[:ATTESTS]->(v)",
                parameters={"fact_id": fact["fact_id"], "ref": ref},
            )

    async def branch_attestations(self, ref: str) -> list[dict]:
        return await asyncio.to_thread(self._branch_attestations, ref)

    def _branch_attestations(self, ref: str) -> list[dict]:
        result = self._conn.execute(
            "MATCH (a:Attestation)-[:ATTESTS]->(v:Value {ref: $ref}) "
            "RETURN a.fact_id, a.operator, a.reason, a.revokes",
            parameters={"ref": ref},
        )
        out = []
        while result.has_next():
            fact_id, operator, reason, revokes = result.get_next()
            out.append({"fact_id": fact_id, "operator": operator,
                        "reason": reason, "revokes": revokes or None})
        return out
