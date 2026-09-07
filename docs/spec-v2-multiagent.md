# Axor Control Plane — Specification v2 (multi-agent)

The single-agent system is **built and running**. This document specifies the multi-agent layer that extends it. It references the shipped system as fact — "this already works this way" — not as a spec to edit. Every compatibility statement is a constraint the new code must meet against the running system, verified by the size-1 regression gate.

**Reading order & dependency:** semantics (labels → delta → case) → execution → secrets → wire. Ch.1 defines the label-authority split every later chapter rests on; Ch.6 is the wire appendix.

## Contents
1. A2A — Intra- and Inter-Federation
2. Governance Delta under Multi-Agent
3. The Multi-Agent EvidenceCase
4. Multi-Agent Runtime Model
5. Federation Vault
6. Protocol Delta (control-plane protocol → v0.2)

## v2 in one breath
A federation is a keyset boundary. Inside it, labels are data and travel with values; across it, foreign labels are claims and are re-derived (Ch.1). Governance delta stops being one number — local integrity, boundary containment, systemic outcome are three measurements, and only event-grounded ones (containment) may be headlined (Ch.2). The EvidenceCase gains a causal_subgraph but stays one-case-per-discrepancy, and a size-1 subgraph is exactly the v0.13 object (Ch.3). At runtime there is no shared governance state: labels ride in message envelopes, gates run locally on both ends of every edge, spawn narrows, death is a fact (Ch.4). A federation has two kinds of secret under opposite rules — tool creds dispensed, operator keys signed-not-surrendered, one wall between (Ch.5). The wire adds JCS-canonicalized signatures and a `context_excision` one-shot (Ch.6).

## Invariants inherited from v0.13 (unchanged, load-bearing)
- EvidenceCase is the primary artifact; quiet-until-wrong.
- Enforcement is local; the control plane is an advisory overlay, never in the decision path.
- One-way rules everywhere: commands/spawn/attestation narrow or preserve, never widen.
- Rule 0: verdict-deciding code is pure kernel, shared by runtime and replay.
- Every backward-compat claim in v2 is a **regression requirement against production**, not a design goal: a tree of size 1 must produce the exact behavior the deployed v0.13 code already produces — byte-identical EvidenceCase, same renders. Multi-agent that alters the size-1 path is a production break, not a new feature.

---

# Chapter 1 — A2A — Intra- and Inter-Federation

Replaces §14.1 of ui-spec v0.13. A2A was specified there as one regime; it is two, and conflating them would either over-restrict internal topologies or under-restrict external ones. This chapter fixes the split. Everything else in v0.13 stands.

---

## 0. The distinction that decides everything: label authority

A **federation** is the set of nodes governed under one operator keyset — the same ed25519 keys the control plane already uses (protocol §6). The federation boundary is a *key boundary*; no new identity machinery is invented.

| | Intra-federation | Inter-federation |
|---|---|---|
| Label authority | one — ours | two — ours and theirs |
| Their labels are | **data**: travel with values, trusted | **claims**: evidence at best, never authority |
| Taint at the boundary | carried intact | re-derived per peer trust level |
| causal_root | one namespace, one graph | foreign root kept as opaque forensic ref; local root minted |
| Control | full on both ends | our side only |
| Sentinel | one graph | per-peer reputation, foreign nodes opaque |

Everything below is a consequence of this table.

---

## 1. Intra-federation A2A — child agents, one federation

Lateral messages between Invokables in one tree (or across hosts within one org) — beyond the parent→child delegation the tree already covers.

- **Labels are carried, not re-derived.** A value tainted at child A arrives at child B still tainted, with its original causal_root. This is the rule that closes the *internal laundering* hole: without it, one lateral hop through a sibling would wash taint. Hop count cleans nothing.
- **A message is still a sink (out) and a source (in)** — the gate pipeline evaluates at every hop. But *peer trust* does not apply: what applies is the receiving node's own posture. A RESTRICTED child receiving a tainted value still cannot export it; being internal grants no permissions, it only preserves label fidelity.
- **Transport is irrelevant to semantics.** In-process or networked, same rules; networked intra edges are signed with federation keys — signature here proves *integrity and origin*, it does not trigger re-derivation (same authority on both ends).
- **Control plane: full.** Both endpoints are in the topology; cascade stop, subtree budgets (§15), per-node interventions already cover lateral edges — the tree becomes a DAG in the view, nothing else changes.
- **Sentinel: one graph.** Lateral flows are just more edges on the same causal_roots.
- **Eval scenarios native to this regime:** fabricated delegation ("sibling claims a task it never ran" — claim-vs-reality across an internal edge), lateral exfil attempt (tainted value routed via a sibling toward a permissive-looking sink — must still be denied by carried taint), message drop/corruption between siblings.

Cost of this regime: none beyond what exists. Most of it is the Invokable tree generalized from strict hierarchy to lateral edges.

---

## 2. Inter-federation A2A — different agents, different keysets

- **Inbound: re-derive, always.** Foreign labels — including "this value is clean" — are claims. Default: inbound values are tainted per the peer's declared trust level, regardless of what the peer asserts. The foreign causal_root is preserved as an opaque reference (forensics, cross-org incident reconstruction) but a local root is minted; the foreign graph is never grafted onto ours.
- **Trust levels apply here and only here:**
  - **L0 (default, undeclared):** full taint on everything inbound; peer is an untrusted export destination outbound. Fail-closed, consistent with §11.2.
  - **L1 (authenticated):** identity is verified, so Sentinel reputation accrues to a stable peer — but inbound taint is unchanged. L1 buys attribution, not trust.
  - **L2 (federated agreement):** the peer's *signed label assertions* are accepted as **evidence with a policy-declared discount** — they may lower default taint by declared, bounded steps for declared message classes, never to clean, never affecting confidentiality floor. The discount table is operator config on our side (inaccessible to runtime, same soundness shape as enum supersession). Exact discount semantics: open question #1.
### 2.1 Declared trust and its ceiling

Config-declared trusted agents map onto the level ladder — declaration is what lifts a peer above L0. The ceiling is deliberate and worth stating as a rule:

> **Declaration buys discount, never label authority.** Trusting a peer in config is a judgment about the peer's *intentions*; label authority requires a guarantee about the peer's *runtime integrity*. A declared-trusted agent can still be prompt-injected — if declaration granted its labels authority, compromising the weakest trusted peer would compromise our taint system transitively.

Only federation membership grants label authority, because it means *we govern that node*: our keyset, our config, our kernel. If an external agent truly warrants child-level trust, the honest path exists — onboard it into the federation (deploy under our keyset and config). There is no `trusted: true` shortcut that grants authority without governance.

**Governed-peer attestation (the legitimate middle).** A peer under a *different* operator may prove it runs under axor-core: a signed attestation of kernel version + config hash. This is trust in *mechanism* rather than in behavior — stronger evidence than a bare L2 agreement, and it permits higher declared discounts (the peer's kernel provably propagates taint, so its label assertions are mechanically produced, not free-form claims). Still not label authority: their operator is not ours, and their config may permit what ours denies. Renders in config as `l2 + governance_attested`, verified at channel establishment and on config-hash change.

- **Outbound: the peer is an export destination.** Confidentiality-floor and taint gates evaluate against the peer's level; sending to an L0 peer is gated exactly like posting to a public channel.
- **Control: our side only.** Foreign peers render in the topology as opaque nodes (distinct glyph, reputation badge) with zero intervention affordances — no pause, no inject, structurally absent, not greyed. Our edge to them is controllable (we can stop *our* node's sends); their internals are not our business and the UI must not pretend otherwise.
- **Sentinel: per-peer reputation** — heat accrues to the peer identity (hence L1's value), attestation of a peer-fed branch works as in §8.1.1 but is scoped to our side of the boundary.
- **Eval scenarios native to this regime:** peer impersonation (L0 presenting as L2 — must fail on keys), label forgery (L2 assertion with invalid signature — must fall to L0 handling, and the fall must be evidenced), cross-org delegation fabrication, confidentiality probe (does our agent leak floor-protected values to an L1 peer under pressure).

Cost of this regime: peer key management, assertion verification, discount policy — real machinery. Hence ordering (below).

---

## 3. Shared by both regimes

- Message = sink/source; EvidenceCase gains a `peer` dimension (with `federation: intra|inter`).
- **Config Builder:** intra peers are auto-derived from the tree — nothing to declare. Inter peers are declared like sinks: identity (pubkey), level, allowed message classes; undeclared = L0.
- **Proxy A2A mode** (v0.13 §14.1) survives as the zero-code entry point for *inter* traffic — sitting on the channel, observing claims vs reality across the org boundary. For intra traffic the adapter already sees everything; a proxy there adds nothing.

## 4. UI consequences

- Topology renders the federation boundary as an enclosure; this is where the graph lens (Control's second mode, from the d3 discussion) earns its existence — cross-boundary edges are exactly the information a list cannot carry.
- Inter edges carry the level badge; a denied cross-boundary export flashes on the edge, at the boundary, where it happened.

## 5. Ordering & decisions (resolved)

Ship order: **intra first** (generalize the tree to lateral edges — small delta, closes internal laundering), **inter second** (key exchange + assertion machinery, driven by first real cross-org demand).

| # | Decision |
|---|---|
| 1 | **Critical sinks ignore L2 discounts entirely.** Discount table = bounded steps per (peer, message class), operator config; criticality trumps it — a discount that could reach a critical sink would make criticality negotiable by the peer. |
| 2 | **MCP-as-A2A is L0/L1-only.** L2 assertion envelope arrives with the native protocol; retrofitting it into MCP metadata would fragment verification. |
| 3 | **Intra lateral edges go direct**, signed with federation keys — never through the plane service. §12.0 extended to data: the advisory layer must not become a data-path dependency. |
| 4 | **Governed-peer attestation = fact-of-governance only** in v1. The config hash pins accountability, not semantics; parsing foreign configs into local trust decisions is out. |


---

# Chapter 2 — Governance Delta under Multi-Agent

Extends §7 of ui-spec v0.13. In single-agent, "governance delta" is one number: same agent, same fault, integrity with vs without axor-core. In a tree that number fractures — a fault injected at one node produces effects (denial, fabrication, containment) at *other* nodes, at other depths, some turns later. This chapter defines what is measured, where, and — the honest part — what must NOT be collapsed into a headline.

Inherits §7's split intact: **(A) enforcement on/off** is the clean, demoable claim; **(B) governance changes behavior** is the subtle research observation. Multi-agent widens the gap between them, so the discipline matters more, not less.

---

## 1. Why one number breaks

A fault at a leaf node in a tree has three distinct governance effects, and they are not the same measurement:

1. **Local** — did the node where the fault landed fabricate or report honestly? (This is the §7 single-agent question, unchanged, per node.)
2. **Propagated** — did the fabrication/taint travel up or across to other nodes, or was it contained at a boundary? This is the effect that *only exists* in multi-agent, and the one governance most distinctively addresses (carried taint, Ch.1; boundary denials).
3. **Systemic** — did the *whole task* succeed honestly, fail honestly, or fail by fabrication? The end-to-end outcome the operator actually cares about.

Reporting a single averaged delta over a tree hides which of these moved. A governed run can improve containment (2) dramatically while local honesty (1) is unchanged and systemic outcome (3) flips from "confident wrong answer" to "honest failure" — three different stories, one useless average.

---

## 2. What gets measured, and where

**Per-node integrity (local).** The §7 metric, computed at each node independently. A tree yields a *vector* of per-node integrities, not a scalar. The Eval tab renders it as the topology (§12.1) colored by integrity, not as a single bar.

**Containment (propagated) — the multi-agent-native metric.** For a fault whose effect is tainted/fabricated content, measure whether it crossed each boundary it reached:
- `contained_at`: the node/edge where propagation was denied (governed) or NULL (escaped).
- Ungoverned twin: where did the same content actually reach? (Usually: the export sink, or the root's final answer.)
- Containment delta = boundaries-held / boundaries-reached. This is new; §7 had no propagation axis because there was nothing to propagate to.

**Systemic outcome (end-to-end).** A three-way label on the whole run, not a number:
- `honest_success` · `honest_failure` (surfaced the problem, didn't complete) · `fabricated_failure` (completed by lying).
- The governance claim in its strongest honest form: **governance converts `fabricated_failure` → `honest_failure`.** It does not manufacture success — saying so would be the §7(B) overreach at tree scale. An agent denied a tainted export doesn't thereby get the right answer; it gets an honest non-answer. That is the win, stated without inflation.

---

## 3. The two-trace object, generalized

§7's side-by-side (ungoverned lies left, governed caught right) becomes a **two-tree** object: the same fault replayed over the topology with and without axor-core, aligned on the fault-injection point. The EvidenceCase gains:
- the `contained_at` boundary marked on the governed tree where the ungoverned tree shows continued propagation — the multi-agent analog of §7's `intent_denied` marker, now placed at a *node boundary* rather than a single timeline point.
- per-node integrity diff, so the operator sees not just "caught" but "caught here, and here it was already honest, and here governance changed the context and behavior shifted (B — flagged, not counted)."

This is the strongest visualization the product has: cause at a leaf, containment at a boundary, honest failure at the root, all in one frame — and it is *only* possible because governance is a deterministic replay (§13), so both trees are re-derived from one recorded fault, not two separate stochastic runs.

---

## 4. The measurement trap, and the rule that avoids it

Multi-agent sharpens §7(B): under governance, upstream nodes receive *different context* (a denial instead of a fabricated result, compressed/taint-limited inputs) and therefore behave differently — cascading. Some of the delta is enforcement catching things (A, countable); some is behavior drift from altered context (B, real but not "enforcement caught it"). At tree scale these entangle across nodes.

**Rule: attribute at the boundary, not at the outcome.** A delta is counted as enforcement (A) only where a specific gate produced a specific denial on a specific edge — a discrete, verifiable event. Everything downstream of that denial (how the parent reacted, whether it then fabricated less) is behavior (B): shown, flagged, never summed into the enforcement claim. The containment metric (§2) is pure-A by construction — it counts boundary denials, which are events. Systemic outcome is A+B entangled and is therefore reported as a *label*, never as a governance-attributed number.

This is the §7 neutrality discipline (demo-hero vs research-caveated) made structural: the containment number is safe to headline because it counts events; the systemic label is safe because it's a label; per-node integrity is safe because it's the unchanged §7 metric. Nothing that entangles A and B is ever rendered as a single governance-caused figure.

---

## 5. Federation scope (ties to Ch.1)

- **Intra-federation:** the whole tree is one measurement domain — containment boundaries include lateral edges (Ch.1 §1), governance delta spans the federation.
- **Inter-federation:** measurement STOPS at the boundary. We measure containment *at* our edge to a foreign peer (did we deny sending a tainted value to an L0 peer — countable, ours). We do NOT measure the foreign agent's integrity — no label authority (Ch.1 §2.1), no visibility, and claiming a delta over an agent we don't govern would be exactly the conflict-of-interest §7 warns against. Foreign nodes are excluded from every per-node vector.

---

## 6. Decisions & open

- **Academic artifact:** the reproducible run reports the per-node integrity vector and the containment metric across models *without* axor-core as the neutral baseline; the two-tree governance comparison ships as a separate, labeled "effect of enforcement" experiment (§7 discipline, unchanged). Systemic-outcome labels are reported, never a systemic "score."
- **Demo:** the two-tree containment visualization (§3) is the hero — single fault, cascade contained — replacing the single-agent split as the lead asset once multi-agent ships.
- Open [verify-by-design]: containment denominator — "boundaries reached" needs the ungoverned twin to define reach; for a fault the ungoverned twin propagates infinitely (to the root), so reach is well-defined (every edge on the path to where it landed). Confirm this holds when propagation branches (fault reaches a fan-out node) — lean: reach = union of all edges the ungoverned taint touched, containment = fraction the governed tree denied.


---

# Chapter 3 — The Multi-Agent EvidenceCase

The EvidenceCase is the primary artifact (ui-spec header constraint). Chapters 1–3 make claims *about* it — carried taint (Ch.1), boundary containment (Ch.2), two-tree diffs (Ch.2) — without defining its structure once agents are more than one. This chapter fixes that structure. If the artifact isn't well-defined, none of the claims resting on it are.

Design constraint carried down: the single-agent EvidenceCase (ui-spec §8) is the degenerate case of this one. A tree of size 1 must produce *exactly* the v0.13 object — no migration, no second format.

---

## 1. What an EvidenceCase is, precisely

A reproducible discrepancy between **observed reality and an agent claim**, plus everything needed to re-derive and adjudicate it. Single-agent, that's: reality | claim | trace | influence ranking. Multi-agent adds one thing and generalizes the rest: **the discrepancy now has a location in a topology, and its causes may be elsewhere in that topology.**

The case is *anchored* at one node — where the claim was made — but its evidence spans the subgraph that produced the claim. A fabrication at the root caused by a fault at a leaf is *one* case anchored at the root, not two.

## 2. Structure

```
EvidenceCase
  anchor:            node_id + seq         # where the discrepancy surfaced
  discrepancy:
    observed:        <reality at the source of the claim>
    claimed:         <what the anchor node asserted>
    verdict:         FABRICATION | OMISSION | ...   # unchanged vocabulary
  causal_subgraph:                          # NEW — the multi-agent core
    nodes:           [ {node_id, integrity, role_in_case} ]
    edges:           [ {from, to, kind: delegation|lateral|peer,
                        carried: {taint, floor, causal_root},
                        gate_verdict} ]
    fault_origin:    node_id + seq | null   # where injected, if a scenario run
    contained_at:    [edge...] | null       # boundaries that denied propagation
  influence_ranking: [...]                   # generalized §8: now ranks across nodes
  federation_scope:  intra | inter          # inter: subgraph stops at our boundary
  replay_ref:        {trace_id, cursor}      # opens in Replay at the anchor
  twin_ref:          {trace_id} | null       # the ungoverned two-tree twin (Ch.2)
```

- **causal_subgraph is the addition.** It is the minimal subgraph whose events causally contribute to the discrepancy — computed by walking causal_root provenance backward from the claim, not the whole tree. A 40-node tree with a 3-node causal chain yields a 3-node case. This keeps cases legible (quiet-until-wrong applied to evidence: show the causes, not the org chart).
- **role_in_case** per node: `origin` (fault landed here), `conduit` (propagated through), `container` (denied propagation here), `anchor` (claimed). One node can hold several.
- **influence_ranking generalized:** §8 ranked which inputs most influenced a single agent's claim; now it ranks across the subgraph — which upstream node/value most influenced the anchor's fabrication. The single-node case collapses this to the §8 behavior exactly.

## 3. Anchoring rule — one discrepancy, one case

The trap multi-agent invites: a single fault causing fabrications at three nodes looks like three cases. Rule:

> **One case per discrepancy, anchored where the claim reached a consequence** — an export, a returned answer, a user-visible assertion. Intermediate fabrications that fed a downstream one are *nodes in that case's subgraph* (role `conduit`), not separate cases.

Exception: if two fabrications reach two *different* consequences (two separate exports), that's two cases — even from one fault — because there are two distinct discrepancies with reality. They share a `fault_origin` and cross-reference, but each is independently adjudicable. Shared cause, separate consequence → separate cases; shared cause, single consequence → one case with a subgraph.

## 4. Containment produces a case too

A denial is not a non-event. When governance *contains* a propagation, that is an EvidenceCase in its own right — anchored at the `container` node, verdict `CONTAINED`, with the subgraph showing what would have propagated. This is the positive-space counterpart to a fabrication case, and it is what the two-tree view (Ch.2 §3) renders: the fabrication case (ungoverned twin) beside the containment case (governed), aligned on `fault_origin`.

Consequence for scoring: containment cases are the countable enforcement evidence (Ch.2 §4, pure-A). Fabrication cases that escaped (`contained_at: null`) are the failures. The ratio is the containment metric — now grounded in cases, not a separate tally.

## 5. Inter-federation cases (ties Ch.1)

- `federation_scope: inter` → the causal_subgraph is truncated at our boundary. Foreign nodes appear as a single opaque `peer` node with its trust level, never expanded — we have no causal visibility past it and no authority to assert its internals (Ch.1 §2.1).
- A discrepancy *at* the boundary is anchorable (our node claimed something about a peer interaction that reality contradicts); a discrepancy *inside* the foreign agent is not our case — we can record that a peer's assertion diverged from a later observation, but the verdict is `PEER_ASSERTION_UNVERIFIED`, not `FABRICATION` — we don't adjudicate agents we don't govern.

## 6. Export & share (ties ui-spec §8.3)

- The causal_subgraph exports as part of the PDF/permalink — reality | claim | verdict | subgraph diagram | replay ref. Still observations only, never raw bodies (§8.3 rule holds across all nodes in the subgraph).
- Inter-federation: the opaque peer node exports as opaque — sharing a case must not leak our view of a peer's identity beyond its declared pubkey.

## 7. Decisions & open

- **Backward compatibility: mechanical.** A size-1 subgraph with the anchor as its only node, `federation_scope: intra`, `twin_ref: null` IS the v0.13 EvidenceCase. Renderers check subgraph size: 1 → the §8 receipt; >1 → the subgraph view. One format, two renders.
- **Storage:** the causal_subgraph is *derived* from the trace (backward provenance walk), not stored redundantly — it's recomputed on case open, deterministic like everything in replay. Cache the walk result keyed by (trace_id, anchor_seq); invalidate never (traces are append-only).
- Open [verify-by-design]: influence ranking across nodes needs a cross-node influence measure; the single-agent method (input ablation) becomes subgraph ablation — replay the case's subgraph with each upstream value removed, rank by verdict change. Deterministic and already supported by §13.2 counterfactuals — confirm cost is acceptable for large subgraphs (lean: bounded by causal chain length, not tree size, so typically small).


---

# Chapter 4 — Multi-Agent Runtime Model

Chapters 1–4 fixed semantics: labels (Ch.1), delta (Ch.2), the case (Ch.3). None said *how the tree executes* — v0.13's `Invokable` was a delegation tree, and lateral edges, boundary containment, and an advisory plane over live nodes raise concrete mechanical questions: where governance state lives, who runs a boundary gate, what happens on spawn and death. This chapter fixes the execution model. It is the load-bearing bridge from semantics to the axor-core port.

Rule 0 still governs: everything here that decides a verdict is pure kernel code, shared by runtime and replay. The runtime adds *orchestration* around the kernel, never governance logic inside the orchestration.

---

## 1. Where governance state lives

**Per-node, local. There is no shared governance state.** Each Invokable owns its own: degradation level, taint set, capability table, budget remainder, fact log. This is not an optimization — it is the same rule as §12.0 (enforcement local, plane advisory) applied to the tree: a shared state store would be a coordination dependency in the decision path, and its availability would gate every node's gates.

Consequence: **labels travel with values, not through a registry.** When node A sends a value to node B (delegation or lateral, Ch.1 §1), the taint/floor/causal_root ride *in the message envelope*. B folds them into its local state on receipt. No node queries a central taint service; there isn't one. This is exactly what makes intra-federation carried-taint (Ch.1) mechanically real rather than aspirational.

## 2. Who runs a boundary gate

A boundary is an edge between two nodes. The gate runs **at the sender, on the send** (outbound sink), and **at the receiver, on the receipt** (inbound source) — two evaluations, each local to one node, never a joint computation.

- Sender-side: the message is a sink; the sender's export/confidentiality gates evaluate against the destination (peer level for inter, sibling posture for intra). A denial here means the message is never sent — containment at the source (Ch.2 `contained_at` = the sender's edge).
- Receiver-side: the message is a source; the receiver folds carried labels and its own gates evaluate on next use. A denial here means the value is received but its use is gated — containment at the destination.

Two independent local evaluations, no distributed transaction, no consensus. This is why boundary containment (Ch.2) needs no new distributed machinery: it is two ordinary local gate runs that happen to sit on either end of one edge.

## 3. Spawn

When a node spawns a child (delegation) or opens a lateral edge:
- **Child inherits a derived posture, not a blank one.** Degradation level: child starts at the parent's level or NORMAL, whichever is *higher* (more restricted) — a CAUTIOUS parent cannot spawn a NORMAL child to escape its own restriction. This closes spawn-laundering, the tree analog of lateral-laundering (Ch.1 §1).
- **Budget: a slice, bounded by the parent's remainder** (§15 subtree budgets, unchanged — restated because spawn is where it's enforced).
- **Capability: intersection, never expansion.** A child's capability table is a subset of the parent's; delegation cannot grant a capability the parent lacks. Spawn narrows or preserves, never widens — the same one-way rule as everywhere.
- **causal_root: inherited**, so the child's provenance chains to the parent's; a fault in a child is traceable to the delegation that created it.
- Spawn is a trace event (`node_spawned`), so replay reconstructs the tree shape deterministically at every cursor.

## 4. Death

- **Normal completion:** node returns its result (a value with its labels) up the edge it was spawned from; result-as-source is gated at the parent (§2 receiver-side). The node's fact log is *retained in the trace*, not discarded — a completed child's facts still contributed to the run and must survive for the EvidenceCase subgraph (Ch.3) and replay.
- **Stop (control plane §12.2):** delivered as governance state, cascades down (children get the signal), node finishes current intent then admits no more — death is orderly, the audit trail stays intact (§12.2, restated for the tree: cascade is child-ward along spawn edges).
- **Crash / disappearance:** the parent's pending receive on that edge times out; the missing result is a `node_stale` fact at the parent (protocol §7 stale semantics, applied to an internal edge). A crashed child cannot silently be treated as having returned clean — absence is a fact, not a success.

## 5. Ordering within the tree

Each node orders its own events by local seq (protocol §3). Across nodes, ordering is established *only* by message causality — a receipt event is after the send event that caused it, by carried reference, not by wall clock. There is no global clock and none is needed: the causal_subgraph (Ch.3) is a partial order over message edges, and replay folds each node's local sequence, stitching across edges by carried causal refs.

This is what makes multi-agent replay deterministic despite concurrency: we never need to know the *interleaving* of two independent nodes' events, only the causal edges between them. Independent events are genuinely unordered and their interleaving cannot affect any verdict (each node's gates see only its local state + carried labels).

## 6. The plane over a live tree (ties §12, Ch.6)

- Each node maintains its own outbound plane connection (protocol §1, restated: the *node* dials out, so a tree of N nodes is N connections, not one multiplexed — a node's control channel dies with the node, no orphaned routes).
- Topology view (§12.1) is assembled from N telemetry streams; the tree shape comes from `node_spawned`/result events, not from a node self-reporting its parent (which it could lie about — structure is derived from traced spawn events, the same events replay uses).
- Cascade stop (§4) is issued as desired-state to the subtree root; each node propagates the signal to its own children on apply — the plane sends one command, the tree distributes it along spawn edges. The plane does not enumerate and command each node (it may not even know the full live shape between heartbeats); it commands a root and the tree does the rest, locally.

## 7. Decisions & open

- **No shared state, ever** is the chapter's spine: labels in envelopes, gates local on both edge ends, spawn narrows, death is a fact. Every one of these is the tree-scale restatement of an existing single-node rule — the multi-agent runtime introduces *orchestration*, zero new governance primitives. This is the property that keeps the axor-core port bounded.
- **Backward compat:** a single node is a tree with no edges — §1–6 degenerate to v0.13 single-agent runtime exactly.
- Open [verify-by-design]: lateral-edge cycles (A→B→A) — carried causal_root makes a value's return to its origin detectable (its own root appears in inbound provenance); lean: not an error, taint just accumulates monotonically and the cycle can't launder (each pass re-folds labels). Confirm no pathological heat inflation on tight cycles; if found, dampen by causal_root identity, not by hop count.
- Open [verify-by-experiment]: N outbound plane connections for large N (100+ node trees) — connection overhead. Criterion: 100-node tree telemetry at heartbeat cadence within one backend instance's SSE budget; if it breaks, an optional per-host telemetry aggregator (one connection per host, not per node) — but the *command* path stays per-node-addressable (a host aggregator must not become a command intermediary, §12.0).


---

# Chapter 5 — Federation Vault

Extends ui-spec §14.2 (single-node vault) to a federation. The load-bearing decision: **a federation has two kinds of secret, and they live under opposite rules.** Merging them into one store subordinates the stricter secret to the looser one and collapses the property the whole security model rests on.

| | Tool credentials | Operator signing keys |
|---|---|---|
| What | API keys for the agents' tools | ed25519 keys that sign commands & define the boundary |
| Purpose | injected at the sink so the agent never sees them | prove command authenticity end-to-end (protocol §6) |
| Vault operation | **dispense** — hand the secret to the proxy at call time | **sign** — accept a payload, return a signature, never the key |
| Centralize? | yes — federation-scoped, shared, rotatable | custody yes, disclosure never |
| If vault compromised | tool creds exposed (bad, bounded) | must remain UNABLE to forge federation commands |

These are two subsystems that happen to share the word "vault." This chapter specifies both and the wall between them.

---

## 1. Federation tool-credential vault (extends §14.2)

The §14.2 vault, scoped to a federation instead of one node.

- **Federation-scoped custody.** One vault holds tool credentials for all nodes in the federation; a node fetches by (tool, endpoint) at call time, injected at the sink (§14.2 unchanged). Nodes no longer each carry their own creds — the convenience the single-node vault couldn't offer.
- **Scope is still per (tool, endpoint), enforced per node.** Federation-wide storage does not mean federation-wide access: a node gets a credential only for a (tool, endpoint) its own config declares. A compromised `web-scraper` cannot pull the `payments` credential just because both live in the same federation vault — enrollment scope is per node, checked at dispense. Shared storage, partitioned access.
- **Injection is still proxy-side, sink-bound, byte-for-byte** (§14.2): the credential exists where the effect happens and nowhere upstream; a prompt-injected agent redirecting a call gets no credential (scope mismatch).
- **Rotation is federation-wide config** (versioned), not a control-plane command — the plane may *revoke* federation-wide in an incident (narrowing), never grant (§12.0 rule extended to the federation).
- **Break-glass: fail closed, federation-wide** (§14.2 decision #14 unchanged): vault down → injection impossible → typed denial at every node. No cached creds anywhere, ever.
- **Inter-federation:** a foreign peer's credentials are never in our vault. We hold, at most, our own credential for authenticating *to* the peer — a tool credential like any other, scoped to that peer endpoint. Their creds are theirs (Ch.1 boundary).

## 2. Operator signing-key custody — vault SIGNS, never surrenders

The federation's ed25519 operator keys (protocol §6) may be *custodied* in a vault, but the operation is inverted and the private key never leaves.

- **Delegated signing, not key dispensing.** For tool creds the vault hands out a secret; for signing keys the vault **takes a payload and returns a signature**. The private key lives in an HSM/KMS-class backend and is never emitted — not to a node, not to the plane, not to the operator's browser. Different API, different verb: `dispense(tool, endpoint) → secret` vs `sign(key_id, payload) → signature`.
- **This preserves the property §6 depends on.** §6's guarantee is that a compromised backend can withhold or delay commands but cannot *forge* them, because signing requires the operator's private key which the backend never holds. Custody-with-delegated-signing keeps this exactly: the vault holds the key, but a caller must be authorized to *request a signature*, and even a compromised vault caller can only get signatures over payloads it submits — it cannot exfiltrate the key to sign offline at leisure or to impersonate the federation elsewhere.
- **Pubkeys are not secrets.** Verification keys stay in local adapter config (protocol §6, unchanged) — never fetched from the vault, so a compromised vault cannot swap the verification keys a node checks against. Custody covers the *private* half only; the public half's whole security value is that it lives where verification happens, pinned.
- **Authorization to sign is itself a scoped, audited capability.** Which operators may request signatures for which federation key is config (multi-operator keyset, parked on team features — protocol §4). Every `sign` request is logged: who, which key, payload hash. A signature is an operator action and belongs in the audit trail beside the command it authorizes.

## 3. The wall between the two

- **Separate backends, separate credentials to reach them.** The tool-cred vault and the signing custody are not one service with two modes; compromising the ability to dispense tool creds must not grant the ability to request signatures. A single "vault admin" role spanning both would recreate the single-point-of-forgery this whole chapter avoids.
- **Different failure blast radius, by design.** Tool-cred vault compromised → tool creds exposed, agents can't be trusted to hold effects, incident is bad but bounded to data access. Signing custody compromised → attacker can request signatures but cannot extract keys; with per-request authorization and audit, forged commands are detectable and scoped, not silent and total. The wall ensures one breach is not both.
- **Peer pubkeys (Ch.1) touch neither vault** — public, config-resident, no custody needed.

## 4. UI (ties §14.2, federation topology)

- Federation settings show two panes, visibly separate: **Tool credentials** (enrolled tools, scope per node, rotation, health) and **Signing keys** (custody backend, operators authorized to sign, sign-request audit log). The separation is not just backend — the UI must not let an operator conflate "manage our API keys" with "manage who can command the federation."
- A `sign` request appears in the same trace/audit surface as the command it produced — an operator action, first-class (§12.3).

## 5. Decisions & open

- **Two secrets, two subsystems, one wall** is the chapter. Tool creds: federation-scoped, dispensed, fail-closed. Signing keys: custodied, signed-not-surrendered, pubkeys pinned in config. Never one store.
- **Backward compat:** a single-node federation with no signing custody is exactly §14.2 — operator keys in local config, tool vault optional per node. The federation vault is additive; nothing about the single-node path changes.
- Open [verify-by-design]: signing-custody latency on the command path — `sign` is a network round-trip to the custody backend per operator command. Commands are low-frequency (operator-issued, not per-agent-step), so latency is tolerable; confirm it stays off the *enforcement* path entirely (enforcement is local and unsigned by operators — §12.0; only operator commands are signed, and those are already interactive-latency-tolerant).
- Open: HSM vs software-KMS for signing custody at the self-hosted tier — self-hosted orgs may not have an HSM; lean: pluggable custody backend (HSM / cloud-KMS / software-keystore), same `sign` interface, security posture declared per deployment — mirrors the vault backend decision (monetization / arch: build on existing stores, don't roll crypto).


---

# Chapter 6 — Protocol Delta (control-plane protocol → v0.2)

Amends control-plane-protocol-v0.1. Three changes and a decisions record. Everything not mentioned stands.

---

## 1. Canonicalization: JCS (RFC 8785) — decided

Signed payloads (`(node_id, version, canonical_state_delta | fact, timestamp)`, protocol §6) are canonicalized with **JCS**. Rationale: a published RFC an auditor recognizes beats a house convention; conforming libraries exist for Python and TS. Deliverable attached to this decision: `test-vectors/jcs-signing.json` — a fixed set of payload → canonical-bytes → signature triples that both the adapter and the plane service verify in CI. A signing implementation that can't reproduce the vectors doesn't ship.

## 2. Third one-shot command: `context_excision`

Self-heal (ui-spec §8.2.1, v0.13) needs a wire form. It joins injection in the one-shot family — the desired-state document gains:

```json
"pending_excision": {
  "id": "exc_4b09",
  "segment_refs": ["cr_8a12", "cr_77e0"],
  "reason": "refusal drift after prompt update, re-anchoring",
  "operator": "op_dmitrii",
  "sig": "ed25519:…"
}
```

Semantics mirror injection exactly (protocol §4): LWW while pending, **at-most-once by id**, consumption reported upstream as `excision_consumed {id}`, replays no-op against the consumed-id set. Adapter-side guards before applying:
- **Provenance guard** (ui-spec §8.2.1 v0.13): every `segment_refs` entry must resolve to a non-operator-config segment; any operator-config ref → `excision_refused {id, reason: "operator_config_provenance"}`, nothing applied — the command is atomic, no partial excision.
- Test-bench flag NOT required (unlike injection): excision is subtractive of non-config content and reason-gated; it is however still an intervention — mid-run it marks the run `intervened`.
- Trace event `context_excision` carries the refs and hashes per persistence rules; taint on already-derived values is untouched.

The one-shot family is now: `pending_injection` (additive, test-bench-gated), `pending_excision` (subtractive, provenance-guarded). Future one-shots follow this template: LWW-pending + consumed-fact + at-most-once by id + adapter-side guard.

## 3. Peer channel hooks (forward declaration, inter-federation)

Not implemented in v0.2; shapes reserved so nothing here has to move when inter-A2A ships (spec v2 Ch.1):
- Channel establishment verifies peer declaration (pubkey, level) from local config; `governance_attested` additionally verifies the signed kernel-version + config-hash attestation, re-verified on config-hash change.
- L2 assertion envelope is native-protocol-only (Ch.1 decision #2); MCP-as-A2A channels are pinned L0/L1 at establishment.

## 4. Decisions recorded in passing

| Item | Decision |
|---|---|
| Multi-operator keyset format | **Blocked on team features** — intentionally parked, not open. Single-operator keyset (list of one) ships; format extension lands with the attestation policy hook (spec decision #8). |
| Heartbeat T=10s, stale=3T | **Verify-by-experiment**: criterion — first live adapter run; stale false-positive rate < 1/day per node on a healthy link, else raise T. |
| Demo composition | **Single-graph demo confirmed; split-screen governed/ungoverned demoted to the landing's second screen.** Single-agent split demoted; two-tree containment is the hero for trees. This is how the running §7 comparison is presented at tree scale. |


---

# Consolidated decisions log (v2)

Resolved during v2 drafting. Chapter references use the v2 numbering above.

| # | Area | Decision |
|---|---|---|
| v2-1 | Label authority (Ch.1) | Federation = operator keyset boundary. Intra: labels are data, carried intact. Inter: foreign labels are claims, re-derived per trust level; foreign causal_root kept opaque, local root minted. |
| v2-2 | Declared trust ceiling (Ch.1) | Config declaration buys bounded discount, never label authority. Only federation membership grants authority. Governed-peer attestation (kernel+config-hash signature) = trust in mechanism, higher discount, still not authority. |
| v2-3 | L2 discounts (Ch.1) | Critical sinks ignore L2 discounts entirely. |
| v2-4 | Inter wire (Ch.1) | MCP-as-A2A is L0/L1-only; L2 assertion envelope is native-protocol-only. |
| v2-5 | Intra transport (Ch.1) | Lateral edges go direct, federation-key-signed — never through the plane. |
| v2-6 | Governed-peer attestation depth (Ch.1) | Fact-of-governance only in v1; config hash pins accountability, not semantics. |
| v2-7 | Delta decomposition (Ch.2) | Three measurements: per-node integrity (vector), containment (event-grounded, headline-safe), systemic outcome (label, never a number). |
| v2-8 | Attribution rule (Ch.2) | Enforcement (A) counted only at a boundary denial; downstream behavior (B) shown/flagged, never summed. |
| v2-9 | Inter-federation measurement (Ch.2) | Measurement stops at our boundary; foreign agent integrity never scored. |
| v2-10 | Case anchoring (Ch.3) | One case per discrepancy, anchored at the consequence; shared cause + single consequence → one case; separate consequences → separate cases. |
| v2-11 | Containment cases (Ch.3) | A denial produces a `CONTAINED` case; containment metric grounded in cases. |
| v2-12 | Case storage (Ch.3) | causal_subgraph derived on open (backward walk), cached by (trace_id, anchor_seq), never stored redundantly. |
| v2-13 | No shared state (Ch.4) | Per-node state only; labels in envelopes; boundary gates are two local runs; spawn narrows; death is a fact; cross-node order by message causality. |
| v2-14 | Two secrets, one wall (Ch.5) | Tool creds: federation-scoped, dispensed, fail-closed, per-node scope. Operator keys: custodied, signed-not-surrendered, pubkeys pinned in config. Separate backends — never one store. |
| v2-15 | Canonicalization (Ch.6) | JCS (RFC 8785); test-vector file gates any signing implementation in CI. |
| v2-16 | Excision command (Ch.6) | `context_excision` one-shot: LWW-pending, at-most-once by id, provenance-guarded (atomic), not test-bench-gated, marks runs `intervened`. |
| v2-17 | Multi-operator keyset (Ch.6) | Parked, blocked on team features. |
| v2-18 | Demo composition (Ch.6) | Two-tree containment is the multi-agent hero; single-agent split demoted. Reflects how the shipped §7 comparison is presented for trees. |

## Open — verify by experiment or design
- Containment denominator on fan-out (Ch.2): reach = union of edges ungoverned taint touched. [design]
- Cross-node influence ranking via subgraph ablation (Ch.3): bounded by causal-chain length. [design]
- Lateral-edge cycles A→B→A (Ch.4): monotone taint, no laundering; check heat inflation on tight cycles. [design]
- N outbound plane connections for 100+ node trees (Ch.4): optional per-host telemetry aggregator, command path stays per-node. [experiment]
- Signing-custody latency on command path (Ch.5): confirm off the enforcement path entirely. [design]
- HSM vs software-KMS custody backend at self-hosted tier (Ch.5): pluggable, posture per deployment. [decision-pending]
- ~~Kùzu single-writer~~ (closed: the stored graph was removed — value refs repeat across runs); TS typegen, heartbeat cadence: carried from architecture v0.1. [experiment]

## Build order

The single-agent runtime is in production; the multi-agent layer is new code on top of it. Order:

1. **Runtime** (Ch.4) — labeled message envelopes + boundary gates on both edge ends, behind the existing single-node path. Regression gate: a size-1 tree produces the exact EvidenceCase the running system already produces. Nothing merges until that holds.
2. **EvidenceCase** (Ch.3) — optional causal_subgraph, derived on open; renderer branches on subgraph size (1 → existing receipt, unchanged).
3. **Measurement** (Ch.2) — containment metric + systemic label, alongside the existing per-node integrity.
4. **Inter-federation** (Ch.1) — peer keysets, trust levels, attestation. Greenfield; touches no running path until an inter edge exists.
5. **Federation vault** (Ch.5) — federation-scoped tool creds + walled signing custody.
6. **Protocol v0.2** (Ch.6) — JCS + context_excision, additive to the running wire.
