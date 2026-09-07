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
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from axor_backend.tenancy import current_org_id, set_current_org


async def register_trace_derivations(
    graph: GraphStore, run_id: str, events: list[dict[str, Any]]
) -> int:
    """Fold an ingested trace into the taint graph: a TOOL_RESULT's produced
    value derives from the input refs of the TOOL_CALL it answers. This is the
    same arg_refs → value_ref provenance the replay kernel folds (see
    replay._derive_driving_root), so the graph and the counterfactual agree on
    what flows where. Returns the number of edges registered.

    A call is paired with its result PER NODE. A kernel event carries no call
    id — only `seq`, `node_id` and the payload — so the pairing is node plus
    order, which is exactly the ordering the kernel guarantees.

    This used to hold one pending list for the whole run, and a run can carry
    several nodes (migration 0004). Interleaved, the graph did not merely miss
    an edge, it recorded a WRONG one: with A calling, B calling, A answering,
    B answering, it wrote `B's input -> A's output` — one node's value declared
    the origin of another's, in the index whose entire job is origin — and lost
    both real edges.
    """
    pending: dict[str, list[str]] = {}
    registered = 0
    for line in events:
        kind = line.get("kind")
        payload = line.get("payload", line)
        node = str(line.get("node_id") or "")
        if kind == "tool_call":
            arg_refs = payload.get("arg_refs") or {}
            pending[node] = [r for r in arg_refs.values() if r]
        elif kind == "tool_result":
            dst = payload.get("value_ref") or line.get("causal_root")
            if dst:
                for src in pending.get(node, ()):
                    await graph.register_derivation(src, dst, run_id)
                    registered += 1
            pending.pop(node, None)
    return registered


async def rehydrate_graph(store: Any, graph: GraphStore) -> None:  # noqa: ANN401
    """Rebuild ONE tenant's taint graph from the persisted event log.

    The graph is a DERIVED index, not a source of truth — every derivation is
    already in the events table (arg_refs/value_ref) and every attestation in the
    facts table. Folding them back in at boot makes the in-memory store durable
    across restarts (and lets a fresh instance catch up) without a graph DB.

    Reads through the ambient tenant (``tenancy.current_org_id``), so the caller
    sets the org first — see :func:`rehydrate_all_graphs`, which is what boot
    calls. Rehydrating without setting one folds the public tenant's runs only,
    which is what happened before the registry existed.
    """
    for run in await store.list_runs():
        lines = [json.loads(raw) for raw in await store.run_events(run["run_id"])]
        await register_trace_derivations(graph, run["run_id"], lines)
    for fact in await store.all_facts():
        if fact.get("fact_type") == "operator_attestation":
            await graph.append_attestation(json.dumps(fact))


async def rehydrate_all_graphs(store: Any, registry: GraphRegistry) -> None:  # noqa: ANN401
    """Boot rehydrate for every tenant, each into its own graph."""
    previous = current_org_id()
    try:
        for org in await store.list_orgs():
            set_current_org(org)
            await rehydrate_graph(store, registry.for_org(org))
    finally:
        set_current_org(previous)


class GraphRegistry:
    """One :class:`GraphStore` per tenant, created on first use.

    The taint graph holds value refs and the run ids they were derived in, which
    is exactly the shape of a tenant's private data — so it cannot be one store
    per process. Before this, a single ``InMemoryGraphStore`` served every org
    and ``/v1/graph/khop`` returned another tenant's refs and run ids to anyone
    who guessed a ref.

    ``factory`` builds a store for one org; the default is the in-memory
    implementation, matching SQLite's role as the dev/test default. A hosted
    deployment passes a factory returning :class:`KuzuGraphStore` with a
    per-tenant DB file — the isolation that class's docstring already promised
    and that only a registry can deliver.

    ``max_open`` bounds how many stores are held at once, closing the least
    recently used beyond it. Only a PERSISTENT store is ever evicted: a Kuzu
    store's data is in its file and reopens on the next request, while an
    in-memory store IS the data and nothing rebuilds it outside boot — evicting
    one would silently empty a tenant's provenance graph, which is worse than
    holding it. So the cap is a bound on open database handles, not a cache
    policy, and it does nothing at all on the in-memory default.
    """

    def __init__(
        self,
        factory: Callable[[str], GraphStore] | None = None,
        max_open: int | None = None,
    ) -> None:
        self._factory: Callable[[str], GraphStore] = (
            factory if factory is not None else lambda _org: InMemoryGraphStore()
        )
        self._graphs: OrderedDict[str, GraphStore] = OrderedDict()
        self._max_open = max_open

    def for_org(self, org: str) -> GraphStore:
        graph = self._graphs.get(org)
        if graph is None:
            graph = self._factory(org)
            self._graphs[org] = graph
            self._evict_beyond_cap()
        else:
            self._graphs.move_to_end(org)
        return graph

    def _evict_beyond_cap(self) -> None:
        if self._max_open is None:
            return
        while len(self._graphs) > self._max_open:
            evictable = next(
                (o for o, g in self._graphs.items()
                 if getattr(g, "persistent", False)),
                None,
            )
            if evictable is None:
                return  # nothing here can be reopened; holding beats losing
            closing = self._graphs.pop(evictable)
            close = getattr(closing, "close", None)
            if close is not None:
                close()

    def current(self) -> GraphStore:
        """The graph of the tenant this request belongs to."""
        return self.for_org(current_org_id())

    @property
    def tenants(self) -> tuple[str, ...]:
        return tuple(sorted(self._graphs))


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
        # (src, dst, run_id) -> the edge. A dict rather than a list because the
        # dedupe used to be `if edge not in self._edges`, a linear scan of a
        # growing list on every edge: 0.011 ms/edge at 500 edges and 0.157 at
        # 8000, which is boot time, before the first request is answered.
        self._edge_index: dict[tuple[str, str, str], dict[str, str]] = {}
        # adjacency, both directions — a taint graph is explored up (provenance)
        # and down (what this tainted), and rebuilding it per khop was a second
        # full pass over every edge on every request.
        self._adj: dict[str, set[str]] = {}
        self._attestations: dict[str, dict] = {}
        self._lock = asyncio.Lock()

    @property
    def _edges(self) -> list[dict[str, str]]:
        return list(self._edge_index.values())

    async def register_derivation(self, src_ref: str, dst_ref: str, run_id: str) -> None:
        async with self._lock:
            key = (src_ref, dst_ref, run_id)
            if key in self._edge_index:
                return
            self._edge_index[key] = {"src": src_ref, "dst": dst_ref, "run_id": run_id}
            self._adj.setdefault(src_ref, set()).add(dst_ref)
            self._adj.setdefault(dst_ref, set()).add(src_ref)

    async def khop(self, focus: str, k: int, limit: int) -> dict[str, object]:
        """Undirected k-hop neighbourhood of `focus` (spec decision 6). Explores
        both directions — a value's provenance (upstream) and what it tainted
        (downstream) — expand-on-click, capped at `limit` nodes."""
        k = int(k)
        limit = int(limit)
        adj = self._adj
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
            e for e in self._edge_index.values()
            if e["src"] in seen and e["dst"] in seen
        ]
        return {"focus": focus, "nodes": sorted(seen), "edges": edges}

    async def append_attestation(self, fact_json: str) -> None:
        """Idempotent on `fact_id`: the graph is rebuilt from the fact log at
        every boot, so folding the same attestation twice must be folding it
        once."""
        async with self._lock:
            fact = json.loads(fact_json)
            self._attestations[str(fact["fact_id"])] = {
                "fact_id": fact["fact_id"],
                "operator": fact.get("operator") or "",
                "reason": fact.get("reason") or "",
                "revokes": fact.get("revokes") or None,
                "covers": list(fact.get("covers", ())),
            }

    async def branch_attestations(self, ref: str) -> list[dict]:
        return [
            {"fact_id": a["fact_id"], "operator": a["operator"],
             "reason": a["reason"], "revokes": a["revokes"]}
            for a in self._attestations.values() if ref in a["covers"]
        ]


class KuzuGraphStore:
    """One writer per tenant DB file; reads share the same connection.

    Every write here is idempotent, because this store is PERSISTENT and the
    graph is rebuilt from the event log at every boot. It was not: derivations
    used `CREATE`, so a restart duplicated every edge it had already stored,
    and attestations used `CREATE` against a `fact_id` primary key, so the
    second boot of any deployment where an operator had ever attested a branch
    raised a duplicate-key error out of `rehydrate` — which lifespan does not
    catch. The backend did not start.
    """

    persistent = True

    def __init__(self, db_dir: str | Path, tenant: str) -> None:
        import kuzu

        Path(db_dir).mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(Path(db_dir) / f"{tenant}.kuzu"))
        self._conn = kuzu.Connection(self._db)
        self._write_lock = asyncio.Lock()
        self._ensure_schema()

    def close(self) -> None:
        """Release the connection and the database handle. The data is in the
        file: the registry reopens this store the next time the tenant is
        served."""
        self._conn.close()
        self._db.close()

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
        # MERGE, not CREATE: the same derivation is folded again on every boot,
        # and the in-memory store has always deduplicated. Two implementations
        # of one Protocol disagreeing about whether an edge is a set member is
        # the same defect twice.
        self._conn.execute(
            "MATCH (a:Value {ref: $src}), (b:Value {ref: $dst}) "
            "MERGE (a)-[:DERIVES {run_id: $run_id}]->(b)",
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
        # MERGE on the primary key and set the rest: `fact_id` IS the identity
        # of an attestation, and re-folding the fact log is the normal case, not
        # an error. `CREATE` here is what stopped a hosted deployment booting a
        # second time.
        self._conn.execute(
            "MERGE (a:Attestation {fact_id: $fact_id}) "
            "SET a.operator = $operator, a.reason = $reason, "
            "a.sig = $sig, a.revokes = $revokes",
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
                "MERGE (a)-[:ATTESTS]->(v)",
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
