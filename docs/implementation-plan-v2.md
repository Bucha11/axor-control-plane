# Axor Control Plane — Implementation Plan: v2 multi-agent layer (v0.1, 2026-07-12)

Derived from: `spec-v2-multiagent.md` (consolidated, six chapters + decisions log v2-1…v2-18) ·
`v2-chapters/` · the four multi-agent mockups (`demo-two-tree-containment.jsx`,
`federation-topology.jsx`, `federation-drilldown.jsx`, `evidencecase-multiagent.jsx`) ·
a per-mechanic gap analysis of the seven repositories as of 2026-07-12.

Companion to `implementation-plan-v0.1.md`, which is fully executed (its §7). This plan is
the v2 sequel: the additive multi-agent layer over the shipped v1 platform. v2 does not edit
v1 or its code; every compatibility claim below is a regression requirement against
production, not a design goal.

---

## 0. Where things actually stand (verified 2026-07-12)

The v2 spec bundle is a 2026-07-05 snapshot; the code has since moved. Corrections to its
assumptions, so the plan starts from reality:

- **Kernel staging is dissolved.** `axor_core.kernel.{events, state, degradation, replay}`
  is merged, purity-pinned (`.importlinter` + `tests/kernel/test_kernel_purity.py`), and
  consumed by both the runtime and the platform replay engine. There is no
  `packages/axor-kernel` anywhere.
- **v2 implementation task #6 (protocol → v0.2) is already shipped**: JCS (RFC 8785)
  canonicalization with committed test vectors (`packages/axor-backend/tests/vectors/jcs.json`),
  `pending_excision` one-shot (LWW, at-most-once by id, provenance-guarded, atomic,
  `intervened` marking), `context_excision` / `excision_refused` kernel events, adapter-side
  `PlaneSession` verification. Chapter 6 needs **verification against the spec text, not
  implementation** (Phase 0).
- The platform is a working product: plane service, replay/counterfactuals, regression CI,
  taint graph, notifications, share/export, EE licensing, Control tab with a live node tree.

### Gap table — v2 mechanic vs current code

| # | v2 mechanic (chapter) | State | Evidence |
|---|---|---|---|
| 1 | Labels ride in message envelopes, folded on receipt (Ch.4) | PARTIAL | Labels travel via `federation/receipt.py:FederationReceipt` + `gateway.py:receive→restore_root`, not via `contracts/envelope.py:ExecutionEnvelope` (no taint/floor/causal_root fields) |
| 2 | Lateral sibling edges, dual gate both ends (Ch.1 §1, Ch.4 §2) | PARTIAL | Strict parent→child tree (`node/spawn.py:LineageSummary`); dual sink/source gating exists only on the federation peer transport |
| 3 | Spawn narrows: degradation max(parent, NORMAL), budget slice, capability ∩, causal chain (Ch.4 §3) | PARTIAL | Capability intersection EXISTS (`spawn.py:_validate_child_policy`); degradation + budget are **shared engines**, not derived/sliced; `CHILD_SPAWNED` is a runtime trace event, not a kernel event |
| 4 | Death is a fact: result-as-source, `node_stale` fact, cascade stop (Ch.4 §4) | PARTIAL | Result-as-source EXISTS (`node/wrapper.py:597-614`); cascade stop EXISTS; `node_stale` is a platform notification (`monitor.py:stale_sweep`), not a parent-side kernel fact |
| 5 | Peer trust L0/L1/L2, opaque foreign root, L2 discount, config-hash attestation (Ch.1 §2) | PARTIAL | `gateway.py:FederationLevel` has L1/L2 only; no L0 tier, no bounded discount (binary restore-vs-remint), foreign roots mapped not kept-opaque, attestation lacks config hash |
| 6 | Kernel events: `node_spawned`, message send/receive, boundary denial (Ch.4 §3, Ch.3) | MISSING | `kernel/events.py:EventKind` has none of them |
| 7 | EvidenceCase: `causal_subgraph`, anchor, `contained_at`, `twin_ref`, `federation_scope`, `role_in_case`, `CONTAINED` (Ch.3) | MISSING | `axor_eval/contracts.py:EvidenceCase` has none of these fields/verdicts |
| 8 | Multi-node platform: topology from spawn events, cross-node replay, federation boundary render (Ch.2, Ch.4 §6) | PARTIAL | Control tab renders a node tree from plane telemetry; taint graph + replay are single-run/single-node; no boundary/graph lens |
| 9 | Sentinel per-peer reputation (Ch.1 §2) | MISSING | Single per-tenant resource graph; no peer dimension |
| 10 | Federation vault: `dispense(tool, endpoint)` + `sign(key_id, payload)` (Ch.5) | MISSING | Only provenance-receipt signing (`federation/signing.py`) and operator-command verification (`axor_backend/signing.py`) exist |

Reusable substrate (don't rebuild): per-value taint inheritance across spawn, capability
intersection, federation gateway with signed receipts, cascade-stop plumbing, versioned
kernel event schema + replay fold, JCS signing, the live node-tree Control UI.

---

## 1. Non-negotiable constraints (audit the plan against these)

- **The size-1 regression gate** (spec header + Ch.4 §7): a tree of size 1 must produce the
  exact behavior deployed v1 produces — byte-identical EvidenceCase, same renders, same
  runtime path. Multi-agent that alters the size-1 path is a production break, not a feature.
  This gate is built **first** (Phase 0) and guards every later phase.
- **Rule 0** unchanged: everything verdict-deciding is pure kernel, shared by runtime and
  replay. The multi-agent runtime adds *orchestration only* — zero new governance primitives
  outside the kernel (v2-13).
- **No shared governance state, ever** (v2-13): per-node state; labels in envelopes; boundary
  gates are two independent local runs; spawn narrows; death is a fact; cross-node order by
  message causality only.
- **Label authority = federation membership** (v2-1, v2-2): intra labels are data, carried
  intact; inter labels are claims, re-derived per trust level. Declaration buys bounded
  discount, never authority. No `trusted: true` shortcut.
- **Attribute at the boundary, not at the outcome** (v2-8): enforcement (A) is counted only
  where a gate produced a denial on an edge; downstream behavior (B) is shown, flagged,
  never summed. Containment is headline-safe because it counts events; systemic outcome is
  a label, never a number (v2-7).
- **One case per discrepancy, one format, two renders** (v2-10): renderers branch on
  subgraph size — 1 → the shipped v1 receipt, >1 → the subgraph view.
- **One-way rules everywhere**: commands/spawn/attestation narrow or preserve, never widen.
- **Intra lateral edges never route through the plane** (v2-5): the advisory layer must not
  become a data-path dependency.
- Quiet-until-wrong: subgraphs show causes, not the org chart; foreign peers are opaque with
  zero intervention affordances — structurally absent, not greyed.

---

## 2. Phases

Ordering follows the spec's own task list (runtime → case → axes → inter → vault), with the
Ch.1 nested order (intra first, inter second) and the two platform-surface phases
interleaved where they unblock demos. Task #6 (protocol v0.2) is verification-only.

### Phase 0 — Spec intake + the size-1 regression gate (~1 week, platform + axor-core)

The gate is the phase's product; nothing multi-agent merges before it exists.

- **Docs**: v2 spec + chapters + four mockups imported (this commit). Doc-hygiene notes per
  spec footer: v2 supersedes ui-spec §14.1 (A2A), generalizes §7/§8, extends §14.2 (vault) —
  add reader notes to `ui-spec-v0.14.md`, do not rewrite shipped sections. Amend §7 demo
  wording per v2-18 (two-tree containment is the hero; split-screen demoted to second screen).
- **Size-1 regression gate, executable form**:
  - axor-core: golden single-node fixture — a recorded production-shape trace committed as a
    fixture; CI job replays it and byte-compares the kernel fold snapshot + gate verdicts
    against committed goldens. Fails on any drift of the single-node path.
  - axor-eval: golden EvidenceCase — serialize a current single-agent case to bytes, commit;
    CI compares post-change serialization of the same inputs.
  - Platform: existing golden-trace test extended to assert the *renderer branch*: a size-1
    case must take the v1 receipt path (snapshot test on the receipt component).
- **Ch.6 verification pass**: diff shipped protocol v0.2 (`control-plane-protocol-v0.2.md`,
  backend + `axor_core.plane`) against spec Ch.6 line by line. Known deltas to check: JCS
  vector file is shared/identical on both adapter and plane sides; peer-channel hook shapes
  (Ch.6 §3) reserved in the protocol doc; one-shot family template documented. Fix gaps as
  doc/test work — no new wire changes expected.

Exit: CI red if anyone breaks the single-node path; Ch.6 confirmed done or gaps ticketed.

### Phase 1 — Runtime generalization (Ch.4, lands in axor-core; ~3–4 weeks)

The load-bearing phase — labels in envelopes + boundary gates, everything else layers on it.
All additive; the existing axor-core suite must pass unmodified (same discipline as the v1
kernel merge, decision §5.1 of the v1 plan).

1. **Envelope-carried labels.** Extend the message path so taint + confidentiality floor +
   causal_root ride *in the envelope* for every inter-node value transfer (delegation result,
   lateral message), folded into the receiver's local state on receipt. Unify with the
   existing receipt machinery rather than duplicating it: the `FederationReceipt` becomes the
   *inter*-federation wire form (claims, verified then re-derived); a lighter
   `CarriedLabels` block on the envelope is the *intra* form (data, folded intact — carriage,
   not re-derivation). One kernel fold function consumes both.
2. **Lateral edges.** Generalize the Invokable topology from tree to DAG: a `LateralChannel`
   between nodes in one federation. Message = sink at the sender (export/confidentiality
   gates against sibling posture) and source at the receiver (fold carried labels, gate on
   next use) — two local evaluations, no joint computation. Transport-irrelevant semantics;
   networked intra edges signed with federation keys (integrity/origin, no re-derivation).
   Direct, never through the plane (v2-5).
3. **Spawn narrowing, made explicit.** Today degradation and budget are *shared engines*
   across the subtree — a valid degenerate case, but not the spec's derived posture. Add,
   additively: child degradation floor = max(parent level, NORMAL) computed at spawn (child
   may run its own engine but can never start below the parent); optional budget *slice*
   mode — child cap bounded by parent remainder at spawn — with the current shared-cap mode
   remaining the default (it already satisfies "bounded by parent"; slice is opt-in config).
   Capability intersection already exists; keep. causal_root chaining: child provenance
   chains to the delegating parent (extend `inherit_value_ledger` with the spawn edge ref).
4. **Death as facts.** Parent-side receive timeout on a spawn/lateral edge → `node_stale`
   fact folded at the parent (kernel), feeding degradation recompute — absence is a fact,
   not a success. Completion and cascade stop already conform; add tree-shape tests.
5. **Kernel event schema, minor bump.** New `EventKind`s: `node_spawned` (parent, child,
   derived posture, edge ref), `message_sent` / `message_received` (edge kind:
   delegation|lateral|peer, carried labels, gate verdict), `node_stale`. Additive minor
   version — replay accepts, old traces unaffected. Runtime `CHILD_SPAWNED` trace event maps
   onto `node_spawned` in the trace→kernel bridge (`plane/bridge.py`).
6. **Multi-node replay.** The fold generalizes: each node's events fold by local seq;
   cross-node stitching by carried causal refs on message events (partial order — no global
   clock, interleaving of independent events provably cannot affect verdicts). `replay()`
   over a multi-node trace returns per-node state trajectories + edge verdicts.
7. **TS types regen** from the extended schema (`scripts/gen_ts_types.py`, existing pipeline).

Exit criteria:
- **Two-node golden trace**: a recorded run with one delegation + one lateral edge replays
  bit-identically (gate verdicts, per-node degradation, taint) — the multi-node Rule 0 test,
  permanent in CI.
- **Internal-laundering adversarial test**: tainted value routed A→sibling→permissive-looking
  sink is denied by carried taint; hop count cleans nothing.
- **Spawn-laundering test**: CAUTIOUS parent cannot obtain a NORMAL child.
- Size-1 gate green; axor-core suite passes unmodified.

### Phase 2 — EvidenceCase generalization (Ch.3, axor-eval + backend; ~2 weeks)

- **Contracts** (`axor_eval/contracts.py`, strictly optional fields so v1 serialization is
  byte-stable): `anchor {node_id, seq}`, `causal_subgraph {nodes[{node_id, integrity,
  role_in_case}], edges[{from, to, kind, carried, gate_verdict}], fault_origin,
  contained_at}`, `federation_scope: intra|inter`, `replay_ref`, `twin_ref`. New verdicts:
  `CONTAINED` (a denial is a case, anchored at the container — v2-11) and
  `PEER_ASSERTION_UNVERIFIED` (we don't adjudicate agents we don't govern).
- **Anchoring rule** in the case assembler: one case per discrepancy, anchored at the
  consequence; intermediate fabrications are `conduit` nodes; separate consequences →
  separate cases sharing `fault_origin` (v2-10).
- **Derivation, not storage** (v2-12): `causal_subgraph` computed on case open by a backward
  provenance walk from the anchor over the (now multi-node) trace; cached by
  `(trace_id, anchor_seq)`, never invalidated (traces append-only). Walk lives beside the
  kernel replay it reuses; backend endpoint `GET /v1/cases/{id}/subgraph`.
- **Influence ranking generalized**: subgraph ablation via existing counterfactual replay —
  remove each upstream value, rank by verdict change. Verify-by-design item: confirm cost
  bounded by causal-chain length (add a benchmark on a synthetic 40-node/3-chain trace).
- **Renderer branch** (frontend + HTML/PDF export): size 1 → shipped receipt, unchanged
  bytes; >1 → subgraph view per `evidencecase-multiagent.jsx` (role-per-node badges,
  governed/twin toggle). Export ships the subgraph diagram, observations only, opaque peer
  stays opaque.

Exit: golden v1 case still byte-identical; a two-node fabrication (fault at child, claim at
root) yields ONE case anchored at the root with a 2-node subgraph; a boundary denial yields
a `CONTAINED` case; both render.

### Phase 3 — Measurement axes + the two-tree hero (Ch.2, axor-eval + backend + frontend; ~2 weeks)

- **Three measurements, never collapsed** (v2-7): per-node integrity vector (§7 metric per
  node, unchanged math); containment = boundaries-held / boundaries-reached, grounded in
  cases (`CONTAINED` vs escaped `contained_at: null`); systemic outcome as a three-way label
  (`honest_success` / `honest_failure` / `fabricated_failure`) — rendered as a label, never
  a number. Attribution rule structural in the scorer: A counted only at boundary-denial
  events; B flagged, excluded from sums (v2-8).
- **Containment denominator** (open, design): reach = union of edges the ungoverned twin's
  taint touched; add the fan-out test case to fix the semantics before the UI shows a ratio.
- **The two-tree object**: same recorded fault replayed over the topology governed and
  ungoverned, aligned on `fault_origin` (deterministic replay makes both trees derivable
  from one recording — no second stochastic run); `twin_ref` links the case pair.
- **Frontend**: `demo-two-tree-containment.jsx` — the multi-agent hero (one fault, cascade
  contained vs escaped, `contained_at` marked at the boundary); Eval tab topology colored by
  per-node integrity. Demo composition per v2-18: two-tree becomes the landing lead; the
  single-agent split-screen demotes to the second screen.
- Inter-federation scope guard even before inter ships: foreign nodes excluded from every
  per-node vector; measurement stops at our boundary (v2-9) — enforced in the scorer now so
  Phase 5 can't regress it.

Exit: eval run over a 3-node scenario produces vector + containment + label; two-tree view
renders from one recorded fault; regression corpus gains the multi-agent scenarios
(fabricated delegation, lateral exfil, message drop — Ch.1 §1 natives).

### Phase 4 — Multi-node platform surfaces (Ch.4 §6, backend + frontend; ~2 weeks, overlaps 3)

- **Topology from traced spawn events**, not self-reported parents: backend assembles the
  tree/DAG from `node_spawned` + result events across N telemetry streams; desired-vs-
  reported divergence per node as today.
- **Cascade stop, tree-distributed**: plane commands the subtree root; each node propagates
  along spawn edges on apply (plumbing exists — verify against the new lateral edges and add
  the multi-node round-trip test).
- **Graph lens** (Control's second mode): `federation-topology.jsx` — enclosure boundary,
  level badges on inter edges, denied cross-boundary export flashing at the edge;
  `federation-drilldown.jsx` — descend into a node's own world, breadcrumb back. Opaque
  inter peers: distinct glyph, reputation badge, zero affordances.
- **N-connections experiment** (open, Ch.4): 100-node tree telemetry at heartbeat cadence
  within one backend instance's SSE budget — synthetic-load script, measure before building
  anything. Fallback if it breaks: optional per-host telemetry aggregator; the command path
  stays per-node-addressable (an aggregator must not become a command intermediary).

Exit: live 3-node run visible as a topology assembled from spawn events; cascade stop
round-trips across the tree; 100-node result recorded (number, not vibes) in `ops-limits.md`.

### Phase 5 — Inter-federation (Ch.1, axor-core + sentinel + platform; ~3 weeks, demand-gated)

Ship order per Ch.1 §5: intra (Phases 1–4) is the prerequisite; inter starts on first real
cross-org demand. Greenfield beyond the existing gateway.

- **Trust ladder rework** (`federation/gateway.py`): add **L0 as the default** (undeclared
  peer: full taint inbound, untrusted export destination outbound, fail-closed); L1 =
  authenticated attribution only (inbound taint unchanged); L2 = signed label assertions
  accepted as evidence with a **bounded, policy-declared discount** — per (peer, message
  class), operator config, inaccessible to runtime, never to clean, never touching the
  confidentiality floor. **Critical sinks ignore L2 discounts entirely** (v2-3).
- **Opaque foreign roots**: stop mapping foreign sources onto local enums; keep the foreign
  causal_root as an opaque forensic ref and mint a local root (v2-1). The foreign graph is
  never grafted onto ours.
- **Governed-peer attestation**: signed kernel-version + config-hash, verified at channel
  establishment and on config-hash change; fact-of-governance only, higher discount ceiling,
  still not label authority (v2-2, v2-6). Extend `receipt.py:_payload` with the config hash.
- **Wire**: L2 assertion envelope native-protocol-only; MCP-as-A2A channels pinned L0/L1 at
  establishment (v2-4); peer-channel hooks from protocol Ch.6 §3 become real.
- **Config Builder**: inter peers declared like sinks — pubkey, level, allowed message
  classes; undeclared = L0. Intra peers stay auto-derived from the tree (nothing to declare).
- **Sentinel per-peer reputation**: heat accrues to peer identity (L1's value); foreign
  nodes opaque in the graph; attestation scoped to our side of the boundary.
- **EvidenceCase**: `peer` edge kind + `federation_scope: inter` truncation (Phase 2 fields
  activate); `PEER_ASSERTION_UNVERIFIED` in anger.
- **Eval scenarios**: peer impersonation (L0 presenting as L2 — fails on keys), label
  forgery (invalid L2 signature → falls to L0, and the fall is evidenced), cross-org
  delegation fabrication, confidentiality probe against an L1 peer.

Exit: adversarial suite for all four scenarios green; a cross-boundary denial renders on the
edge in the graph lens; discount table round-trips config → gate → case evidence.

### Phase 6 — Federation vault (Ch.5, new backend subsystems; ~2–3 weeks, demand-gated)

Two subsystems, one wall (v2-14). Never one store, never one admin role.

- **Tool-credential vault** (extends the §14.2 single-node story): federation-scoped
  custody; `dispense(tool, endpoint) → secret` checked against the *requesting node's own
  config scope* at dispense (shared storage, partitioned access); injection stays
  proxy-side, sink-bound, byte-for-byte; rotation = versioned federation config; the plane
  may revoke federation-wide, never grant; break-glass fail-closed (vault down → typed
  denial everywhere, no cached creds ever). Foreign peers' creds never enter our vault.
- **Operator signing custody**: `sign(key_id, payload) → signature`; the private key never
  leaves the backend — pluggable custody (HSM / cloud-KMS / software-keystore, posture
  declared per deployment — the open decision, resolved as pluggable); authorization to
  sign is a scoped, audited capability; every sign request logged (who, key, payload hash)
  and surfaced beside the command it authorized (§12.3). **Pubkeys stay pinned in local
  adapter config** — never served by the vault, so a compromised vault can't swap
  verification keys. Verify-by-design item: signing latency stays entirely off the
  enforcement path (only interactive operator commands are signed).
- **The wall**: separate services, separate credentials to reach them, different blast
  radii; UI shows two visibly separate panes (Tool credentials | Signing keys) in
  federation settings.

Exit: prompt-injected agent redirecting a call gets scope-mismatch denial (test);
compromised-dispenser test cannot obtain a signature; sign audit log renders in the trace
surface; single-node deployment with no vault behaves exactly as today (backward compat).

---

## 3. Milestone → demo mapping

| After phase | You can show |
|---|---|
| 0 | CI that fails on any single-node behavior drift — the license to build v2 at all |
| 1 | Two-node golden replay: delegation + lateral edge, bit-identical refold; laundering attempts denied |
| 2 | One fault at a leaf → ONE EvidenceCase at the root with a causal subgraph; a denial as a `CONTAINED` case |
| 3 | **The two-tree hero**: same fault, cascade contained vs escaped, one frame — the new lead demo (v2-18) |
| 4 | Live topology assembled from spawn events; graph lens with a federation enclosure; cascade stop across a tree |
| 5 | Cross-org: forged L2 assertion falls to L0 on camera; denial flashes at the boundary edge |
| 6 | Credential never upstream of the sink; a signature minted under audit without the key ever surfacing |

---

## 4. Risks, named

- **Spawn-posture reshaping vs production.** Degradation/budget are shared engines today;
  the spec wants derived posture per node. Additive-only: shared mode stays the default and
  the degenerate case; slice/floor semantics are opt-in until proven. The size-1 gate is the
  tripwire — if it ever needs "updating" to pass, the change is wrong, not the gate.
- **Envelope/receipt unification.** Two label-carriage mechanisms (envelope intra, receipt
  inter) folding through one kernel function is the clean shape; the tempting shortcut —
  reusing receipts for intra — would make intra labels *claims* and reopen the laundering
  hole. Hold the Ch.1 table.
- **Kernel schema evolution.** Replay refuses unknown majors; all Phase 1 additions must be
  a minor bump with old-trace tests in CI, or every recorded production trace dies.
- **N-connection scalability** (open, experiment): measure at Phase 4 before designing an
  aggregator; if one is needed, telemetry-only — the command path stays per-node.
- **Scope magnetism of inter-federation.** L2 discounts, attestation depth, foreign-config
  parsing all invite widening. v2-3/v2-4/v2-6 are the fixed lines; anything past them is a
  spec change, not an implementation decision.
- **Solo bandwidth.** Phases 0–3 are a complete, demoable slice (multi-agent within one
  federation + the hero demo). Phases 5–6 are explicitly demand-gated; if anything slips,
  cut from 5–6, never from the Phase 0–1 exit criteria.

---

## 5. Decisions log

v2 decisions v2-1…v2-18 are inherited verbatim from `spec-v2-multiagent.md` (consolidated
decisions log) and are binding on this plan. Plan-level decisions added here:

| # | Question | Decision |
|---|---|---|
| p2-1 | Where does v2 runtime work land? | In `axor-core` directly (kernel is already merged; no staging package resurrected). Platform pins releases, dependency direction stays one-way. |
| p2-2 | Ch.6 protocol delta | Already shipped in v1 execution; Phase 0 verifies against spec text instead of re-implementing. |
| p2-3 | Budget slice vs shared cap | Shared subtree cap remains default (satisfies "bounded by parent"); per-child slice is opt-in config. Additive, size-1-safe. |
| p2-4 | Envelope vs receipt | Envelope `CarriedLabels` = intra carriage (data); `FederationReceipt` = inter claims (verified, re-derived). One kernel fold consumes both. |
| p2-5 | Phase gating | Phases 0–4 scheduled now; 5 (inter) and 6 (vault) start on first real cross-org / enterprise-custody demand respectively, per Ch.1 §5 ship order. |

Open items (tracked, with their resolution phase): containment denominator on fan-out →
Phase 3; subgraph-ablation cost → Phase 2 benchmark; lateral-cycle heat inflation → Phase 1
adversarial test; N plane connections → Phase 4 experiment; signing-custody latency →
Phase 6 design check; HSM vs software-KMS → resolved as pluggable custody (Phase 6);
heartbeat T=10s/stale=3T → first live multi-node run (Phase 4).

---

## 6. Out of scope of this plan

- Multi-operator keyset format — parked, blocked on team features (v2-17).
- Foreign-config parsing into local trust decisions (v2-6 fixes attestation at
  fact-of-governance).
- Multi-model side-by-side, batch eval export, continuous drift monitoring, full agent-loop
  codegen — bundle non-goals, unchanged.
- axor-daemon and axor-classifier-simple development (independent ecosystem packages; the
  daemon executor is orthogonal to label carriage).

---

## 7. Work-to-repo mapping

| Repo | Phases | Work |
|---|---|---|
| `axor-core` | 0, 1, 5 | size-1 golden fixtures; envelope labels, lateral edges, spawn narrowing, death facts, kernel events, multi-node replay; trust ladder + opaque roots + attestation |
| `axor-eval` | 0, 2, 3 | golden case bytes; EvidenceCase v2 fields + `CONTAINED`; measurement axes, two-tree scorer, multi-agent scenarios |
| `axor-control-plane` | 0, 2, 3, 4, 6 | gate CI + Ch.6 verification; subgraph endpoint + renderer branch; two-tree UI + demo; topology/graph lens + N-connection experiment; vault subsystems + settings panes |
| `axor-sentinel` | 5 | per-peer reputation dimension, boundary-scoped attestation |
| `axor-probe` | — | no v2 work (self-heal wire form already shipped) |
| `axor-daemon`, `axor-classifier-simple` | — | out of scope |
