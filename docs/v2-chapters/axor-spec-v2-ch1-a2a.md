# Axor Control Plane — Spec v2 (draft), Chapter 1: A2A

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
