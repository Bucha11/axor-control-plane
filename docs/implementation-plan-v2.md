# Axor Control Plane — Implementation Plan v2 (multi-agent) (2026-07-12)

Derived from: `spec-v2-multiagent.md` (Ch.1–6 + consolidated decisions v2-1…v2-18) · the four
v2 mocks in `mockups/v2/` · `ui-spec-v0.14.md` · `control-plane-protocol-v0.2.md` ·
`implementation-plan-v0.1.md` (execution log §7) · the verified current state of this repo and
the pinned ecosystem packages.

The single-agent platform is **built and running** (plan v0.1 §7: Phases 0–6 executed). This
plan covers only the multi-agent layer on top of it. Spec build order (spec v2 "Build order")
is kept: Runtime → EvidenceCase → Measurement → Inter-federation → Vault → protocol leftovers.

---

## 0. Where things actually stand (verified 2026-07-12)

### Already done — v2 items that turn out to be shipped

| Spec v2 item | State in this repo |
|---|---|
| Ch.6 §1 JCS canonicalization | **Done.** `axor_backend/signing.py` delegates to `axor_core.kernel.canonicalize`; test vectors at `packages/axor-backend/tests/vectors/jcs.json`, exercised in CI (`test_jcs_vectors.py`). Spec names the deliverable `test-vectors/jcs-signing.json` — alignment is a rename/symlink, not code. |
| Ch.6 §2 `context_excision` one-shot | **Done.** `pending_excision` in the one-shot consumption path (`plane.py:238`), provenance guard in `axor_core.kernel`, protocol note already at v0.2. |
| Ch.6 heartbeat T=10s, stale=3T | Done (protocol v0.2, plane heartbeat handling). |
| Ch.4 spawn narrowing (partial) | axor-core `node/spawn.py` validates child policy ≤ parent ceiling (export-mode rank, depth); `LineageSummary` carries `parent_id`/`ancestry_ids`. |
| Ch.4 plane-per-node connections | The protocol is already per-node (`/v1/plane/{node_id}/…`); N nodes = N SSE connections by construction. |
| Ch.4 death: stale semantics | `node_stale` exists as a plane broadcast type. |
| Cascade stop over a subtree | Exists (`plane.py:75`) — but as backend-side BFS over a self-reported `parent` field; Ch.4 §6 changes both the mechanism and the source of tree shape (see M2). |

### Not done — the actual v2 surface

| Area | Current reality |
|---|---|
| Topology model | **None.** No nodes/edges tables; "topology" = distinct node_ids in `desired_state`/`reported_state` + an optional `parent` string inside the desired-state blob. No lateral edges, no edge kinds, no federation boundary. |
| Labeled message envelopes / lateral A2A | None anywhere. The proxy's one governed node is hardwired `allow_children=False, depth=0` (`governed.py:96-98`). |
| Multi-agent EvidenceCase | `EvidenceCaseDto` is single-node (reality/claim/verdict/fault_attribution); `causal_subgraph`, roles, `contained_at`, `twin_ref`, `CONTAINED` verdict — all absent. Frontend never renders even the existing `influence` field. |
| Containment metric / systemic label / two-tree | Absent (Eval delta is the single-agent §7 comparison). |
| Graph lens in Control | Absent. ControlTab is a flat list; the only live graph is `TaintGraph.tsx` (value provenance, circular SVG). `ExpertView` has a *static* tree mock. |
| Inter-federation peers | axor-core ships `federation/` (receipts, gateway, ed25519, L1/L2) but **its L2 semantics differ from spec v2** — see Risks. Nothing surfaced in this repo: no peer config, no trust-level ladder L0/L1/L2+attested, no discount table, no per-peer Sentinel reputation. |
| Federation vault | Absent. No credential dispensing, no signing custody. Operator keys: verify-only keyring in the backend, private keys with the operator. |
| Ch.6 §3 peer channel hooks | Not in the protocol note yet (forward declaration only — doc work). |

### Ecosystem dependency model

Platform pins PyPI releases: `axor-core>=0.9.1,<0.10`, `axor-eval>=0.1.0,<0.2`. Runtime work
(M1) and kernel work (M3 walk, M4 twin fold) land in ecosystem repos on the same feature
branch, platform bumps pins (or a git ref mid-flight, as in plan v0.1 Phase 1 §6). Dependency
direction stays one-way: platform consumes, never the reverse.

---

## 1. Non-negotiable constraints (v2 additions on top of plan v0.1 §1)

- **The size-1 regression gate precedes everything** (spec header): a tree of size 1 must
  produce the *byte-identical* EvidenceCase and the same renders the deployed v0.13 path
  produces. This gate lands first (M0) and every phase merges only under it.
- **No shared governance state** (v2-13): per-node state, labels ride in envelopes, boundary
  gates are two independent local runs. Nothing in the backend may become a label registry.
- **Structure from traced events, not self-report** (Ch.4 §6): tree shape derives from
  `node_spawned`/result events — the same events replay uses — never from a node claiming its
  parent. (The current `parent`-field cascade violates this; M2 fixes it.)
- **Label authority = keyset membership only** (v2-1/v2-2): declared trust buys bounded
  discount, never authority; critical sinks ignore discounts entirely (v2-3).
- **Attribution at the boundary** (v2-8): only discrete gate denials count as enforcement (A);
  everything downstream is behavior (B) — shown, flagged, never summed. Containment is
  headline-safe; systemic outcome is a label, never a number.
- **One case per discrepancy, anchored at the consequence** (v2-10); a denial is a `CONTAINED`
  case (v2-11); causal_subgraph is derived on open, cached by `(trace_id, anchor_seq)`, never
  stored (v2-12).
- Rule 0, quiet-until-wrong, advisory overlay, fail-closed — unchanged from v0.1.

---

## 2. Phases

### M0 — Docs, mocks, and the regression gate (~3 days)

The gate before the work:

- Land `docs/spec-v2-multiagent.md` and `mockups/v2/{two-tree-containment, federation-topology,
  federation-drilldown, evidencecase-multiagent}.jsx` in-repo (this commit).
- Protocol note: add Ch.6 §3 peer-channel forward declarations (channel establishment verifies
  peer declaration from local config; `governance_attested` re-verified on config-hash change;
  MCP-as-A2A pinned L0/L1 at establishment; L2 assertion envelope native-protocol-only). Wire
  shapes reserved, not implemented.
- Test-vector deliverable aligned to the spec name: publish `test-vectors/jcs-signing.json` at
  repo root (generated from the existing `tests/vectors/jcs.json` + signature triples over a
  fixed ed25519 test key), verified by both backend CI and (via axor-core PR) adapter CI.
- **Size-1 regression gate in CI**: record the current production path (proxy → claim →
  EvidenceCase JSON → receipt render) as golden fixtures; a CI job replays them and diffs
  byte-for-byte (backend JSON) + snapshot (frontend receipt render via the existing Playwright
  e2e). This job never gets edited to pass — a diff means the change broke production behavior.

Exit: CI has the gate; docs internally consistent; nothing else changed.

### M1 — Runtime: envelopes + boundary gates (axor-core, ~2–3 weeks; platform consumes)

Ch.4, executed in `axor-core` (same feature branch there), consumed here via pin bump.

1. **Labeled message envelope.** Taint set / confidentiality floor / causal_root ride in the
   message on every edge (delegation result, lateral send). Receiver folds carried labels into
   local state on receipt. Pure kernel types (`axor_core.kernel.events` gains
   `message_sent` / `message_received` with `edge_kind: delegation|lateral|peer` and a
   `carried: {taint, floor, causal_root}` block); the fold handles them so replay stitches
   cross-node order by carried refs (Ch.4 §5 — partial order, no global clock).
2. **Boundary gates on both edge ends.** Sender: message-as-sink through the existing
   export/confidentiality gate pipeline, destination = sibling posture (intra). Receiver:
   message-as-source, carried labels folded, gates evaluate on next use. Two local runs — no
   new distributed machinery, per v2-13.
3. **Lateral edges.** Generalize the Invokable tree to lateral sends between siblings within
   one federation, direct transport, federation-key-signed (v2-5 — never through the plane).
   Carried taint closes internal laundering (Ch.1 §1). Cycle handling per the open item:
   monotone re-fold by causal_root identity, no hop-count damping.
4. **Spawn hardening.** Add degradation-level inheritance — child starts at
   `max(parent_level, NORMAL)` (spawn-laundering closed); capability intersection and budget
   slice already validated in `spawn.py` — extend tests to assert all four one-way rules.
   `node_spawned` trace event carries everything replay needs to rebuild the tree.
5. **Death.** Normal return = value + labels up the spawn edge, gated at the parent; crash =
   parent-side receive timeout → `node_stale` fact (absence is a fact, never a clean return);
   stop cascades child-ward along spawn edges (adapter-side, see M2 item 3).
6. **Un-hardwire the proxy's governed node** (`axor_proxy/governed.py`): allow a demo tree
   (orchestrator → researcher → scraper + one lateral edge) so the platform has a real
   multi-node source before real users do.

Exit criteria:
- Golden-trace test at tree scale: record a 3-node run, replay the fold → bit-identical
  verdicts/taint/degradation across all nodes, cross-node order stitched by causal refs only.
- **Size-1 gate green** — the M0 fixtures replay unchanged through the new kernel.
- axor-core suite passes unmodified (the invasiveness gauge, as in plan v0.1 Phase 1).

### M2 — Topology: model, ingest, graph lens (~2 weeks, backend + frontend)

The platform learns that nodes form a graph. Reference mocks: `federation-topology.jsx`,
`federation-drilldown.jsx`.

1. **Backend topology derivation.** New tables `topo_nodes` / `topo_edges` (migration 0004),
   materialized from ingested trace events only: `node_spawned` → delegation edge,
   `message_sent/received` → lateral/peer edge, results/`node_stale` → liveness. Never from
   self-reported `parent`. `GET /v1/plane/topology` returns nodes (level, heat, budget,
   liveness) + edges (kind, last gate_verdict) + federation boundary grouping.
2. **Telemetry**: `plane.py` ingest recognizes the new event kinds; per-edge denial events
   republished on the run stream (feeds the boundary-flash UX, Ch.1 §4).
3. **Cascade stop per Ch.4 §6.** Plane sends one signed command to the subtree root;
   the adapter propagates child-ward along spawn edges. Backend BFS retires to a fallback for
   pre-M1 adapters (and its 409-when-signed limitation dies with it — root command is signed
   like any command).
4. **Frontend graph lens** — Control's second mode (list stays default, quiet-until-wrong):
   - `TopologyGraph.tsx`: hand-rolled SVG per the mocks (house pattern from `TaintGraph.tsx`,
     no d3): federation enclosure (dashed rect), solid delegation / dashed lateral /
     double-stroke violet inter edges, level-colored rings, heat glow, opaque peer glyph
     (rotated square + lock, **zero intervention affordances — structurally absent, not
     greyed**), boundary marker `⇥` on inter edges.
   - Selection card: level/heat, Pause/Resume, attest link (self nodes); the "we govern our
     edge, not their internals" card for peers (mock copy).
   - Drilldown (`federation-drilldown.jsx`): breadcrumb stack, descend into a node's subtree,
     leaf interior = that node's event stream + governance state with Review-&-attest (reuses
     the existing ControlTab detail machinery).
   - Denied cross-boundary export flashes on the edge at the boundary (SSE-driven).
5. Layout: deterministic tiered layout (depth → row) — good enough for ≤100 nodes; no force
   sim (replay determinism of renders, and the mocks are fixed-position anyway).

Exit: live demo tree from M1 renders in the graph lens; pause/stop round-trips from the graph;
cascade stop goes root-command + tree-distribution; size-1: single node renders as today's
list + detail, gate green.

### M3 — The multi-agent EvidenceCase (~2 weeks, kernel walk + backend + frontend)

Ch.3. Reference mock: `evidencecase-multiagent.jsx`.

1. **Kernel: backward provenance walk** (pure, Rule 0 — lands in `axor_core.kernel`):
   `causal_subgraph(trace, anchor_seq)` walks causal_root provenance backward from the claim →
   minimal subgraph `{nodes: [{node_id, integrity, role_in_case}], edges: [{from, to, kind,
   carried, gate_verdict}], fault_origin, contained_at}`. Roles: origin/conduit/container/
   anchor (a node can hold several). A 40-node tree with a 3-node causal chain yields a 3-node
   case.
2. **Anchoring rule** (v2-10) in the eval/audit layer: one case per consequence; intermediate
   fabrications become `conduit` nodes; two consequences from one fault = two cases sharing
   `fault_origin` with cross-references.
3. **`CONTAINED` cases** (v2-11): a boundary denial mints a case anchored at the container,
   subgraph showing what would have propagated. Verdict vocabulary extended:
   `CONTAINED`, `PEER_ASSERTION_UNVERIFIED` (inter, Ch.3 §5 — we never adjudicate foreign
   internals).
4. **Backend: derive-on-open.** EvidenceCase storage stays exactly `runs.evidence_json`
   (v0.13 shape — byte-compat is the point). New endpoint
   `GET /v1/runs/{run_id}/cases/{i}/subgraph` computes the walk on demand, cached by
   `(trace_id, anchor_seq)`, invalidated never (v2-12; traces are append-only). `twin_ref`
   filled when M4's twin exists, else null.
5. **Frontend: one format, two renders.** `EvidenceCase.tsx` branches on subgraph size:
   1 → the existing receipt, **untouched code path**; >1 → the multi-agent render per mock:
   receipt (observed at origin / claimed at anchor) + subgraph SVG with role legend +
   folded influence ranking + governed/twin toggle (toggle appears in M4). Export/share
   include the subgraph diagram; observations only; opaque peers export opaque (Ch.3 §6).
6. **Influence ranking via subgraph ablation** (open item, verify-by-design): reuse the
   existing counterfactual replay — replay the case's subgraph with each upstream value
   removed, rank by verdict change. Bounded by causal-chain length; enforce a chain-length
   cap and render "not computed" beyond it rather than approximating.

Exit: the M1 demo tree's fabrication produces one case, anchored at the orchestrator, 3-node
subgraph, ranking ranks the scraper's fabricated value first; size-1 gate green (receipt
byte-identical, renderer path untouched).

### M4 — Measurement: containment, systemic outcome, two-tree (~1.5 weeks)

Ch.2. Reference mock: `two-tree-containment.jsx`.

1. **Eval: the ungoverned twin.** Both trees are re-derived from one recorded fault via
   deterministic replay (§13) — never two stochastic runs. `axor-eval` grows a twin-fold mode:
   replay with gates evaluated-but-not-enforced → where the taint *would* reach = the
   containment denominator (reach = union of edges ungoverned taint touched — the fan-out
   answer from the open item).
2. **Metrics** (v2-7/v2-8): per-node integrity **vector** (existing §7 metric per node, never
   averaged); containment = boundaries-held / boundaries-reached (pure-A, counts gate-denial
   events, headline-safe); systemic outcome ∈ {honest_success, honest_failure,
   fabricated_failure} — a **label**, never a number. Strongest honest claim, rendered as
   such: governance converts fabricated_failure → honest_failure.
3. **Backend:** scenario-delta API returns the vector + containment + labels + `twin_ref`;
   regression rows for containment scenarios (must-block generalizes to "boundary must hold").
4. **Frontend:**
   - Two-tree containment view per mock: same topology twice, scripted playback of the
     recorded fault, divergence at the boundary, per-edge table ("carried, not laundered" /
     "DENIED — contained here"), systemic-outcome strip. This is the demo hero (v2-18);
     `demo/DemoLanding.tsx` re-composes — single-agent split demotes to the landing's second
     screen.
   - Eval tab: topology colored by per-node integrity (vector, not a bar); B-effects flagged
     ("behavior shifted under governed context"), never counted.
5. **Federation scope of measurement** (v2-9): foreign nodes excluded from every vector;
   containment measured only at our edge.

Exit: single fault on the demo tree → two-tree view plays end-to-end from recorded data;
containment 2/2 + labels rendered; nothing entangling A and B appears as a number anywhere.

### M5 — Inter-federation A2A (~3 weeks; gated on first real cross-org demand, per spec ordering)

Ch.1 inter regime. Intra shipped in M1–M2; this phase is greenfield until an inter edge exists.

1. **axor-core federation alignment (the delicate part — see Risks).** Map the existing
   `FederationGateway` ladder onto spec v2: gateway-"L2 restore" is only valid *intra*
   (same keyset — labels are data); for a different keyset the ladder becomes
   L0 (full taint) / L1 (attribution only, taint unchanged) / L2 (signed assertions =
   **bounded discount, never clean, never confidentiality floor**) / `+governance_attested`
   (kernel+config-hash attestation → higher declared discounts, still no authority).
   Foreign causal_root kept as opaque forensic ref, local root minted, never grafted.
2. **Discount table** = operator config on our side, per (peer, message class), bounded steps;
   **critical sinks ignore discounts entirely** (v2-3) — enforced in the kernel gate, not UI.
3. **Config Builder: peers declared like sinks** — identity (pubkey), level, allowed message
   classes, discount rows; undeclared = L0, fail-closed. Channel establishment verifies the
   declaration; attested peers re-verified on config-hash change (protocol hooks from M0).
4. **MCP-as-A2A pinned L0/L1** at establishment (v2-4); proxy A2A mode survives as the
   zero-code *inter* entry point (claims-vs-reality across the org boundary).
5. **Sentinel: per-peer reputation** — heat accrues to peer identity (L1's value); foreign
   nodes opaque in the graph.
6. **Outbound = export destination**: confidentiality-floor/taint gates evaluate against peer
   level; sending to L0 gated exactly like posting publicly.
7. **Eval scenarios** (Ch.1 §2): peer impersonation (fails on keys), label forgery (falls to
   L0, and the fall is *evidenced*), cross-org delegation fabrication, confidentiality probe.

Exit: a declared L1 peer renders opaque in topology with our edge controllable; forged L2
assertion produces an evidenced fall to L0; discount cannot reach a critical sink (kernel test).

### M6 — Federation Vault (~2–3 weeks)

Ch.5. Two subsystems, one wall — never one store (v2-14).

1. **Tool-credential vault** (extends §14.2): federation-scoped custody, `dispense(tool,
   endpoint) → secret` checked against the *calling node's own config scope* (shared storage,
   partitioned access); injection stays proxy-side, sink-bound, byte-for-byte; rotation =
   versioned federation config; plane may **revoke** federation-wide, never grant; break-glass
   fail-closed (vault down → typed denial at every node, no cached creds ever). Peer creds
   never stored — at most our credential *to* the peer, scoped like any tool cred.
   Lands as a new module in `axor-backend` (storage + dispense API) + proxy-side injection
   hook; pluggable secret backend (env/file → cloud KMS later).
2. **Signing custody**: `sign(key_id, payload) → signature`, private key never emitted
   (HSM/KMS-class backend, pluggable per the open item — same interface, posture declared per
   deployment). Authorization-to-sign is a scoped, audited capability; every sign request
   logged (who, key, payload hash) and surfaced beside the command it authorized (§12.3).
   Pubkeys stay pinned in local adapter config — never fetched from any vault.
3. **The wall**: separate backends, separate credentials to reach them; deploy as a separate
   service (or at minimum separate process + separate auth) so "vault admin" for creds ≠
   "may request signatures". CI check: no code path imports both.
4. **UI**: Federation settings, two visibly separate panes — Tool credentials (enrollment,
   per-node scope, rotation, health) and Signing keys (custody backend, authorized operators,
   sign-request audit log).

Exit: dispense denied on scope mismatch (compromised scraper can't pull payments cred);
sign path works with software-keystore backend; single-node federation with no custody
behaves exactly as §14.2 today (backward compat per Ch.5).

---

## 3. Milestone → demo mapping

| After | You can show |
|---|---|
| M1 | 3-node governed tree runs; recorded trace replays bit-identically across nodes; size-1 gate in CI |
| M2 | Graph lens live: enclosure, lateral edge, drilldown, signed cascade stop via root command |
| M3 | One fault at a leaf → one case at the root with a 3-node causal subgraph + influence ranking |
| M4 | **The hero**: two-tree containment playback — lie spreads left, stops at the boundary right; containment 2/2; fabricated_failure → honest_failure |
| M5 | Opaque peer in topology; forged assertion evidenced falling to L0 |
| M6 | Federation vault: scope-mismatch denial + audited delegated signing |

M4 is the new lead asset (v2-18); everything after it is demand-driven (spec ship order:
intra first, inter on first real cross-org demand).

---

## 4. Risks, named

- **axor-core federation L2 vs spec v2 (the one real semantic conflict).**
  `FederationGateway` today *restores* provenance at L2 ("we trust the peer's labels") —
  spec v2 forbids exactly this for foreign keysets ("declaration buys discount, never label
  authority", never-to-clean). Resolution is scoping, not deletion: restore is legitimate only
  where the keyset is ours (intra = carried labels); the inter ladder is new code. This must
  be reconciled in axor-core *before* M5 builds on it, and the restore path must be
  key-boundary-gated in M1 already (a lateral edge uses it; an inter edge must not).
- **Size-1 byte-compatibility is fragile by nature.** Every phase touches shared paths
  (kernel events, EvidenceCase JSON, renderer). The M0 golden-fixture gate is the only honest
  defense — treat any "small" fixture regeneration as a production break requiring explicit
  sign-off, not a CI fix.
- **Topology-from-events vs the existing `parent` field.** Two sources of tree shape will
  coexist during M2 (old adapters self-report). Derived-from-trace wins on conflict; the
  fallback BFS is clearly marked deprecated and dies with pre-M1 adapters.
- **SSE fan-out at 100+ nodes** (open item, experiment). N per-node connections is the
  protocol's shape; criterion — 100-node tree at heartbeat cadence within one backend
  instance. If it breaks: per-host *telemetry* aggregator only; the command path stays
  per-node-addressable (an aggregator must never become a command intermediary, §12.0).
- **Kùzu single-writer under multi-node ingest.** The per-tenant writer lock (plan v0.1,
  loose end 4) now serializes N nodes' derivation writes; watch it in the M1 demo-tree load;
  fallback remains the queue, not a different database.
- **Ablation cost on deep chains** (M3): bounded by chain length in theory; cap it and render
  honestly ("not computed") rather than approximating — an approximate influence ranking is
  worse than none (it's evidence).
- **Scope magnetism, federation edition.** The peer card invites "just one" intervention
  affordance on foreign nodes; the spec is explicit — structurally absent, not greyed. Hold
  the line at review.

---

## 5. Decisions inherited (not re-litigated here)

All v2-1 … v2-18 from the spec's consolidated log are taken as fixed inputs. Open items land
inside the phase that touches them (fan-out denominator → M4; ablation cost → M3; cycles →
M1; SSE fan-out → M2 exit load test; signing latency → M6, off the enforcement path by
construction; HSM-vs-KMS → M6 pluggable backend).

## 6. Out of scope

- Multi-operator keyset format — parked, blocked on team features (v2-17).
- Parsing foreign configs into local trust decisions (v2-6: fact-of-governance only).
- Foreign-agent integrity scoring of any kind (v2-9).
- Live re-execution fork, hosted multi-tenant scaling work beyond the SSE experiment.

---

## 7. Execution log (2026-07-12)

Executed in one pass, branches `claude/control-plane-implementation-64ld8u`
in `axor-control-plane` and `axor-core`:

| Phase | Status | Proof |
|---|---|---|
| M0 | done | `test-vectors/jcs-signing.json` (7 triples, verified through OperatorKeyring); size-1 golden fixtures + `test_size1_gate.py` (byte-diff, never regenerated to pass); `golden-receipt.spec.ts` render gate; protocol §6a peer hooks |
| M1 | done (axor-core 0.9.2) | NODE_SPAWNED/MESSAGE_SENT/MESSAGE_RECEIVED kinds; pure send gate + carried-root fold in `kernel.messaging`; `replay_tree()` per-node fold; `node.messaging` envelope/bus (signed, tamper-rejected); spawn `inherit_degradation` (narrow-or-preserve, opt-in `per_node_degradation`); crash → CHILD_STALE → FACT node_stale; bridge mappings. 1016 passed |
| M2 | done | events unique per (run,node,seq) (migration 0004); `GET /v1/plane/topology` derived from traced events only; signed cascade = one root command; TopologyGraph lens + opaque PeerCard |
| M3 | done | kernel `causal_subgraph` walk (7 tests); derive-on-open + cache; influence via excision-ablation; ex_tree CONTAINED case; renderer branches on anchor (size-1 path untouched) |
| M4 | done | `containment_report` (pure-A held/reached, carried rows informational); systemic outcome label pair; TwoTreeContainment hero view |
| M5 | done | kernel ladder `federation.ladder` (L0/L1/L2+attested, bounded discount never-to-clean, forged→L0 evidenced, critical sinks ignore discounts); gateway restore scoped to own keyset; Config Builder peer declarations; opaque peer + denied edge in demo/graph; **ladder wired into the live inbound path** (peer-edge delivery re-derives via `receive_foreign`, foreign root kept as opaque forensic ref); **peer-channel establishment** (`establish_channel`: MCP pinned L0/L1, governance attestation verified, failures evidenced); **per-peer Sentinel reputation** (axor-sentinel `PeerReputation`: heat to authenticated identity only, sentinel invariants A-1/A-3) |
| M6 | done | `vault_creds` (dispense/scope/fail-closed/rotate/revoke-narrowing) + `vault_signing` (sign-not-surrender, per-request auth, audit) + wall (separate tokens, AST import test); Settings two panes |
| Final | done | axor-core 1016 passed; platform 155 unit + 13 backend-e2e + 49 Playwright passed; ruff clean on the platform |

**Release gate:** the platform branch now requires **axor-core 0.9.2 on PyPI**
(pins bumped; `uv.lock` still resolves 0.9.1 — regenerate after the release).
Local dev/CI in this session ran against the branch build via editable install.

### 7.1 Follow-through pass (2026-07-12, same session)

- **Real multi-node runtime path**: `axor-proxy` `spawn_governed_tree` /
  `POST /axor/governed/spawn-tree` — three REAL IntentLoop nodes over the
  axor-core message bus (authentic per-node verdicts, labels in envelopes,
  export denied at the orchestrator's own loop); trace + CONTAINED case
  uploaded; `governed-tree.spec.ts` drives spawn → topology graph → causal
  subgraph end-to-end. No canned verdicts anywhere in this path.
- **Ladder in the live inbound path** (axor-core): peer-edge delivery
  re-derives through `receive_foreign`; foreign root kept as opaque forensic
  ref beside the minted local root in MESSAGE_RECEIVED.
- **Peer-channel establishment** (axor-core): `establish_channel` — MCP
  pinned L0/L1 (v2-4), governance attestation verified/evidenced (v2-6).
- **Per-peer reputation** (axor-sentinel): heat accrues to authenticated
  identity only; sentinel scoring invariants reused. 185 passed there.
- **Ch.4 SSE/scale experiment, first data point**: 100-node tree ingest +
  topology derivation through one backend instance under 5s, pinned in CI
  (`test_100_node_tree_within_one_instance_budget`). The live-fleet
  heartbeat-cadence half still needs a real adapter fleet.
- Final: axor-core 1025 · sentinel 185 · platform 158 unit + 13 backend-e2e
  + 50 Playwright — all green.

Deferred (out of scope by decision): axor-eval twin-fold as a separate module
(the two-tree twin is derived in the backend from ONE recorded trace, which is
what the spec's determinism constraint actually requires); axor-probe items;
multi-operator keyset (parked, v2-17); **release gate** — axor-core 0.9.2 to
PyPI, then regenerate `uv.lock` (local dev runs `uv run --no-sync` against the
branch build).
