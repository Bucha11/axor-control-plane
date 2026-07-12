# Axor Control Plane — Spec v2 (draft), Chapter 4: The Multi-Agent EvidenceCase

The EvidenceCase is the primary artifact (ui-spec header constraint). Chapters 1–3 make claims *about* it — carried taint (ch1), boundary containment (ch3), two-tree diffs (ch3) — without defining its structure once agents are more than one. This chapter fixes that structure. If the artifact isn't well-defined, none of the claims resting on it are.

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
  twin_ref:          {trace_id} | null       # the ungoverned two-tree twin (ch3)
```

- **causal_subgraph is the addition.** It is the minimal subgraph whose events causally contribute to the discrepancy — computed by walking causal_root provenance backward from the claim, not the whole tree. A 40-node tree with a 3-node causal chain yields a 3-node case. This keeps cases legible (quiet-until-wrong applied to evidence: show the causes, not the org chart).
- **role_in_case** per node: `origin` (fault landed here), `conduit` (propagated through), `container` (denied propagation here), `anchor` (claimed). One node can hold several.
- **influence_ranking generalized:** §8 ranked which inputs most influenced a single agent's claim; now it ranks across the subgraph — which upstream node/value most influenced the anchor's fabrication. The single-node case collapses this to the §8 behavior exactly.

## 3. Anchoring rule — one discrepancy, one case

The trap multi-agent invites: a single fault causing fabrications at three nodes looks like three cases. Rule:

> **One case per discrepancy, anchored where the claim reached a consequence** — an export, a returned answer, a user-visible assertion. Intermediate fabrications that fed a downstream one are *nodes in that case's subgraph* (role `conduit`), not separate cases.

Exception: if two fabrications reach two *different* consequences (two separate exports), that's two cases — even from one fault — because there are two distinct discrepancies with reality. They share a `fault_origin` and cross-reference, but each is independently adjudicable. Shared cause, separate consequence → separate cases; shared cause, single consequence → one case with a subgraph.

## 4. Containment produces a case too

A denial is not a non-event. When governance *contains* a propagation, that is an EvidenceCase in its own right — anchored at the `container` node, verdict `CONTAINED`, with the subgraph showing what would have propagated. This is the positive-space counterpart to a fabrication case, and it is what the two-tree view (ch3 §3) renders: the fabrication case (ungoverned twin) beside the containment case (governed), aligned on `fault_origin`.

Consequence for scoring: containment cases are the countable enforcement evidence (ch3 §4, pure-A). Fabrication cases that escaped (`contained_at: null`) are the failures. The ratio is the containment metric — now grounded in cases, not a separate tally.

## 5. Inter-federation cases (ties ch1)

- `federation_scope: inter` → the causal_subgraph is truncated at our boundary. Foreign nodes appear as a single opaque `peer` node with its trust level, never expanded — we have no causal visibility past it and no authority to assert its internals (ch1 §2.1).
- A discrepancy *at* the boundary is anchorable (our node claimed something about a peer interaction that reality contradicts); a discrepancy *inside* the foreign agent is not our case — we can record that a peer's assertion diverged from a later observation, but the verdict is `PEER_ASSERTION_UNVERIFIED`, not `FABRICATION` — we don't adjudicate agents we don't govern.

## 6. Export & share (ties ui-spec §8.3)

- The causal_subgraph exports as part of the PDF/permalink — reality | claim | verdict | subgraph diagram | replay ref. Still observations only, never raw bodies (§8.3 rule holds across all nodes in the subgraph).
- Inter-federation: the opaque peer node exports as opaque — sharing a case must not leak our view of a peer's identity beyond its declared pubkey.

## 7. Decisions & open

- **Backward compatibility: mechanical.** A size-1 subgraph with the anchor as its only node, `federation_scope: intra`, `twin_ref: null` IS the v0.13 EvidenceCase. Renderers check subgraph size: 1 → the §8 receipt; >1 → the subgraph view. One format, two renders.
- **Storage:** the causal_subgraph is *derived* from the trace (backward provenance walk), not stored redundantly — it's recomputed on case open, deterministic like everything in replay. Cache the walk result keyed by (trace_id, anchor_seq); invalidate never (traces are append-only).
- Open [verify-by-design]: influence ranking across nodes needs a cross-node influence measure; the single-agent method (input ablation) becomes subgraph ablation — replay the case's subgraph with each upstream value removed, rank by verdict change. Deterministic and already supported by §13.2 counterfactuals — confirm cost is acceptable for large subgraphs (lean: bounded by causal chain length, not tree size, so typically small).
