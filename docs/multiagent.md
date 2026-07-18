# Multi-agent (spec v2)

When you run a tree of agents, one bad tool call at a leaf can become the
orchestrator's confident answer three hops later. The single-agent loop —
catch the discrepancy, replay it, prevent it — still holds; multi-agent Axor
makes the *propagation* visible, contained, and provable. This page is the
deep dive; the top-level README keeps only the teaser.

## Topology graph lens (Control)

The tree rendered as a graph, derived only from traced
`node_spawned`/message events (never self-reported parents): federation
enclosure, delegation/lateral/peer edge kinds, denied sends flashed on the
edge, opaque foreign peers with structurally-zero intervention affordances.
Cascade stop with operator keys is one signed command to the subtree root —
the tree distributes it.

## Multi-agent EvidenceCase

One case per discrepancy, anchored at the consequence, with a **causal
subgraph** derived on open (roles: origin / conduit / container / anchor) and
an influence ranking by deterministic subgraph ablation. A size-1 case is
byte-identical to the single-agent receipt — enforced by a golden regression
gate that is never regenerated to pass CI.

Put plainly: "who lied first" is a diagram, not a debate. See which agent
actually changed the outcome — not merely which agents appeared in the trace.

## Containment & the two-tree view

The demo hero: one recorded fault over both worlds; boundaries-held /
boundaries-reached counts only discrete gate denials (event-grounded), intra
hops render as "carried, not laundered"; the systemic outcome is a label pair
(`fabricated_failure → honest_failure`), never a score.

## Real governed tree

`POST /axor/governed/spawn-tree` (or the Control button) runs three REAL
`axor_core` IntentLoop nodes over the message bus: the scraper's web taint is
carried up two delegation hops in labeled envelopes and the orchestrator's own
gate denies the export. No canned verdicts on this path.

## Inter-federation peers

Declared like sinks in the Config Builder (pubkey, L0/L1/L2 +
`governance_attested`, discount message classes); undeclared = L0, declaration
buys a bounded discount, never label authority; critical sinks ignore
discounts entirely. A compromised partner can't launder taint into your tree.

## Federation Vault

Two subsystems, one wall: tool credentials are *dispensed* (per-node scope
checked at dispense, fail-closed, revoke-only-narrowing); signing keys are
*signed-not-surrendered* (private half never leaves custody, every request
audited). Separate access tokens; an AST test keeps the modules import-free of
each other. Both panes render in Settings.
