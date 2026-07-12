# Axor Control Plane — Spec v2 (draft), Chapter 5: Multi-Agent Runtime Model

Chapters 1–4 fixed semantics: labels (ch1), delta (ch3), the case (ch4). None said *how the tree executes* — v0.13's `Invokable` was a delegation tree, and lateral edges, boundary containment, and an advisory plane over live nodes raise concrete mechanical questions: where governance state lives, who runs a boundary gate, what happens on spawn and death. This chapter fixes the execution model. It is the load-bearing bridge from semantics to the axor-core port.

Rule 0 still governs: everything here that decides a verdict is pure kernel code, shared by runtime and replay. The runtime adds *orchestration* around the kernel, never governance logic inside the orchestration.

---

## 1. Where governance state lives

**Per-node, local. There is no shared governance state.** Each Invokable owns its own: degradation level, taint set, capability table, budget remainder, fact log. This is not an optimization — it is the same rule as §12.0 (enforcement local, plane advisory) applied to the tree: a shared state store would be a coordination dependency in the decision path, and its availability would gate every node's gates.

Consequence: **labels travel with values, not through a registry.** When node A sends a value to node B (delegation or lateral, ch1 §1), the taint/floor/causal_root ride *in the message envelope*. B folds them into its local state on receipt. No node queries a central taint service; there isn't one. This is exactly what makes intra-federation carried-taint (ch1) mechanically real rather than aspirational.

## 2. Who runs a boundary gate

A boundary is an edge between two nodes. The gate runs **at the sender, on the send** (outbound sink), and **at the receiver, on the receipt** (inbound source) — two evaluations, each local to one node, never a joint computation.

- Sender-side: the message is a sink; the sender's export/confidentiality gates evaluate against the destination (peer level for inter, sibling posture for intra). A denial here means the message is never sent — containment at the source (ch3 `contained_at` = the sender's edge).
- Receiver-side: the message is a source; the receiver folds carried labels and its own gates evaluate on next use. A denial here means the value is received but its use is gated — containment at the destination.

Two independent local evaluations, no distributed transaction, no consensus. This is why boundary containment (ch3) needs no new distributed machinery: it is two ordinary local gate runs that happen to sit on either end of one edge.

## 3. Spawn

When a node spawns a child (delegation) or opens a lateral edge:
- **Child inherits a derived posture, not a blank one.** Degradation level: child starts at the parent's level or NORMAL, whichever is *higher* (more restricted) — a CAUTIOUS parent cannot spawn a NORMAL child to escape its own restriction. This closes spawn-laundering, the tree analog of lateral-laundering (ch1 §1).
- **Budget: a slice, bounded by the parent's remainder** (§15 subtree budgets, unchanged — restated because spawn is where it's enforced).
- **Capability: intersection, never expansion.** A child's capability table is a subset of the parent's; delegation cannot grant a capability the parent lacks. Spawn narrows or preserves, never widens — the same one-way rule as everywhere.
- **causal_root: inherited**, so the child's provenance chains to the parent's; a fault in a child is traceable to the delegation that created it.
- Spawn is a trace event (`node_spawned`), so replay reconstructs the tree shape deterministically at every cursor.

## 4. Death

- **Normal completion:** node returns its result (a value with its labels) up the edge it was spawned from; result-as-source is gated at the parent (§2 receiver-side). The node's fact log is *retained in the trace*, not discarded — a completed child's facts still contributed to the run and must survive for the EvidenceCase subgraph (ch4) and replay.
- **Stop (control plane §12.2):** delivered as governance state, cascades down (children get the signal), node finishes current intent then admits no more — death is orderly, the audit trail stays intact (§12.2, restated for the tree: cascade is child-ward along spawn edges).
- **Crash / disappearance:** the parent's pending receive on that edge times out; the missing result is a `node_stale` fact at the parent (protocol §7 stale semantics, applied to an internal edge). A crashed child cannot silently be treated as having returned clean — absence is a fact, not a success.

## 5. Ordering within the tree

Each node orders its own events by local seq (protocol §3). Across nodes, ordering is established *only* by message causality — a receipt event is after the send event that caused it, by carried reference, not by wall clock. There is no global clock and none is needed: the causal_subgraph (ch4) is a partial order over message edges, and replay folds each node's local sequence, stitching across edges by carried causal refs.

This is what makes multi-agent replay deterministic despite concurrency: we never need to know the *interleaving* of two independent nodes' events, only the causal edges between them. Independent events are genuinely unordered and their interleaving cannot affect any verdict (each node's gates see only its local state + carried labels).

## 6. The plane over a live tree (ties §12, ch2)

- Each node maintains its own outbound plane connection (protocol §1, restated: the *node* dials out, so a tree of N nodes is N connections, not one multiplexed — a node's control channel dies with the node, no orphaned routes).
- Topology view (§12.1) is assembled from N telemetry streams; the tree shape comes from `node_spawned`/result events, not from a node self-reporting its parent (which it could lie about — structure is derived from traced spawn events, the same events replay uses).
- Cascade stop (§4) is issued as desired-state to the subtree root; each node propagates the signal to its own children on apply — the plane sends one command, the tree distributes it along spawn edges. The plane does not enumerate and command each node (it may not even know the full live shape between heartbeats); it commands a root and the tree does the rest, locally.

## 7. Decisions & open

- **No shared state, ever** is the chapter's spine: labels in envelopes, gates local on both edge ends, spawn narrows, death is a fact. Every one of these is the tree-scale restatement of an existing single-node rule — the multi-agent runtime introduces *orchestration*, zero new governance primitives. This is the property that keeps the axor-core port bounded.
- **Backward compat:** a single node is a tree with no edges — §1–6 degenerate to v0.13 single-agent runtime exactly.
- Open [verify-by-design]: lateral-edge cycles (A→B→A) — carried causal_root makes a value's return to its origin detectable (its own root appears in inbound provenance); lean: not an error, taint just accumulates monotonically and the cycle can't launder (each pass re-folds labels). Confirm no pathological heat inflation on tight cycles; if found, dampen by causal_root identity, not by hop count.
- Open [verify-by-experiment]: N outbound plane connections for large N (100+ node trees) — connection overhead. Criterion: 100-node tree telemetry at heartbeat cadence within one backend instance's SSE budget; if it breaks, an optional per-host telemetry aggregator (one connection per host, not per node) — but the *command* path stays per-node-addressable (a host aggregator must not become a command intermediary, §12.0).
