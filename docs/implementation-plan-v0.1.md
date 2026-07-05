# Axor Control Plane — Implementation Plan (v0.2, 2026-07-05)

Derived from: `ui-spec-v0.13.md` · `architecture-v0.1.md` · `control-plane-protocol-v0.1.md` ·
`monetization-v0.1.md` · the 7 mockups in `mockups/` · the bundle repo skeleton · the current
state of the seven GitHub repositories.

v0.2: the §5 open questions are resolved (operator decisions, 2026-07-05) — §5 is now a
decisions log; Phase 1 rewritten for a **minimally invasive** kernel merge; **any-custom-agent
connectivity** promoted to a launch requirement (Phases 2/4 patched).

Everything below is sequenced for a solo developer. Phases are ordered by the funnel
(spec §3): value must be demonstrable at the end of each phase, not only at the end.

---

## 0. Where things actually stand (verified 2026-07-05)

| Repo | State | Relevant to plan |
|---|---|---|
| `axor-core` | Mature: `governor`, `taint/`, `degradation/`, `capability/`, `policy/`, `trace/`, `budget/`, `federation/`, `contracts/`, plus `kernel/` containing **adjudicator, decidability, registration** (NOT the staging kernel) | Merge target for the staging kernel; source of the gate pipeline to port |
| `axor-eval` | Real package: `deprivation/`, `runner/`, `replay/`, `audit/`, `governed/`, `contracts.py` | Proxy interprets its scenario specs; backend imports its scorers |
| `axor-probe` | Real package: `probes/`, `shadow/`, `comparator/`, `repair/` (self-heal), `pipeline/`, `signals/` | Health panel verdicts; self-heal → context excision wiring |
| `axor-sentinel` | Real package + `bench_run.py`, docs | Graph semantics behind `GraphStore`; attestation + heat |
| `axor-daemon` | Real package: process-isolated capability executor | External dep; out of platform scope (note in §6) |
| `axor-classifier-simple` | Real package: ML classifiers implementing axor-core protocols | External dep; out of platform scope |
| `axor-control-plane` | **Empty** (README only) | Everything in this plan lands here unless stated otherwise |

Bundle's deliberate loose ends, restated as work items:
1. Gate pipeline port axor-core → kernel + replay fold loop → **Phase 1**
2. `context_excision` missing from protocol note → **Phase 0** (doc) + **Phase 1** (event/fold)
3. Spec title still "Axor Eval" → **Phase 0**
4. Kùzu single-writer-per-tenant vs ingest path → **Phase 3 spike, before committing layout**

---

## 1. Non-negotiable constraints (recap, so the plan can be audited against them)

- **Rule 0:** replay is the same code as enforcement. One pure kernel, two consumers.
- **EvidenceCase is the primary artifact**; every screen produces/displays/aggregates cases.
- **Quiet until wrong**: one hero element per screen; density earned only by problems.
- **Advisory overlay**: enforcement is local; the plane never enters the decision path;
  commands cannot widen; disconnect-safe; e2e ed25519.
- **Fail-closed**: undeclared sink/peer/argument = denied; detection fills names, never classes.
- **Observe-only on the Eval surface**; all writes live on Control; `intervened` runs are fenced.

---

## 2. Phases

### Phase 0 — Bootstrap the platform repo (~1 week)

Import the bundle skeleton into `axor-control-plane` and make it honest:

- Commit skeleton: `packages/axor-kernel` (staging), `packages/axor-proxy`,
  `packages/axor-backend`, `frontend/`, `scripts/gen_ts_types.py`, `docker-compose.yml`,
  root `pyproject.toml` (uv workspace).
- **Run the kernel tests** (`uv sync && uv run pytest`) — written but never executed in the
  no-network build env. Fix what breaks before building on top.
- CI: ruff + pytest + purity contract test; frontend typecheck job.
- Docs pass: rename spec title ("Axor Eval" → platform name, loose end 3); bump protocol
  note to v0.2 adding `context_excision` (event shape: causal_root refs + hashes of removed
  values, per spec §8.2.1) and the §9 open items: **JCS RFC 8785** for signature
  canonicalization + a committed test-vector file (decided, §5.3); static heartbeat
  T=10s, stale=3T.

Exit: `docker compose up` gives a healthy empty backend; CI green; docs internally consistent.

### Phase 1 — Kernel consolidation (the "next real code task", ~2–3 weeks)

The load-bearing engineering of the whole platform. Two kernels currently exist:
`axor_core.kernel` (adjudicator/decidability/registration) and the staging
`packages/axor-kernel` (events, desired-state lattice, degradation recompute, replay fold).
They must become one pure submodule inside axor-core.

**Decision (§5.1): minimally invasive.** The merge is strictly additive — no renames, no API
breaks, no restructuring of existing axor-core modules. Concretely:

1. **Module layout**: staging modules land *beside* the existing three —
   `axor_core.kernel.{events, state, degradation, replay}` added; `adjudicator`,
   `decidability`, `registration` untouched, byte-for-byte.
2. **Gate pipeline via extraction + delegation shims, not rewrite.** Pure functions over the
   event sequence are extracted from `governor.py`/`taint/`/`capability/`; the original
   call sites become one-line delegations to the kernel functions. Public APIs, signatures,
   and behavior of the runtime stay identical — the existing axor-core test suite must pass
   **unmodified** after the port (that is the invasiveness gauge). If a gate resists clean
   extraction, it stays in the runtime for now and is listed as not-yet-replayable rather
   than force-restructured.
3. **Event schema** (versioned JSONL, Pydantic) becomes the single contract: runtime trace
   writer, proxy recorder, backend storage, replay all import it. Schema-version field on
   every line; replay refuses unknown majors.
4. **Add `context_excision`** as a first-class event: fold removes the segment's future
   influence deterministically; derived taint stays (spec §8.2.1). Provenance guard lives
   in the kernel (operator-config segments are not excisable) so runtime and replay agree.
5. **Purity contract**: extend axor-core's `.importlinter` to forbid I/O/framework imports
   inside `axor_core.kernel`; port `test_purity_contract.py`.
6. Merge into axor-core (its own PR there), then **delete `packages/axor-kernel`** from the
   platform and switch imports to `axor_core.kernel`. **No PyPI release required mid-plan**:
   the platform pins a git ref (`axor-core @ git+…@<sha>`) until the next natural axor-core
   release; uv workspaces handle this cleanly.
7. `scripts/gen_ts_types.py`: pick generator (recommend `json-schema-to-typescript` from the
   pnpm side — arch open item), wire `pnpm gen:types`.

Exit criteria (this is the phase's definition of done):
- Golden-trace test: run a governed scenario live, record the trace, replay the fold →
  **bit-identical gate verdicts, degradation trajectory, taint graph**. This test is the
  concrete form of Rule 0 and stays in CI forever.
- axor-core CI green with kernel merged; platform imports `axor_core.kernel`.

### Phase 2 — Proxy path: first external value (~2 weeks)

The funnel entry. Ships independently of the plane service.

**Decision (§5.2): any custom agent must be connectable — launch requirement, not tail.**
The proxy path is inherently framework-agnostic (any agent that calls HTTP tool endpoints
connects by swapping base URLs — no SDK, no imports); keep it that way: nothing in the proxy
may assume a framework, an SDK, or even that the agent is Python.

- `axor-proxy`: httpx+asyncio passthrough; auth forwarded byte-for-byte; exactly two
  intervention points (fault injection, observation recording to JSONL). No raw bodies
  persisted by default; armed/disarmed lifecycle (503 when disarmed, decision #1).
- Fault injection driven by **declarative scenario specs interpreted from `axor_eval.deprivation`**
  — no scenario logic reimplemented in the proxy (arch §2).
- **Demo-mode mock tools**: two archetypes, `web_search` + generic MCP tool (decision #2),
  served by the proxy itself — zero-creds first contact.
- Budget-mismatch counting at the proxy (claimed vs metered calls) — cheap, already core coverage.
- Distribution: `uvx axor-proxy` + Docker image.
- EvidenceCase assembly for the proxy-visible subset (observed reality | claim | trace ref),
  scored via `axor_eval` scorers, written locally as files first (backend optional).

Exit: onboarding steps 1–3 are real against a live agent; one genuine EvidenceCase
("fabricated tool result on timeout") produced end-to-end through the proxy.

### Phase 3 — Backend core + plane service (~3 weeks)

**3a. Ingest & storage.** FastAPI; Postgres append-only JSONB events + materialized views;
trace JSONL files on disk/object storage with PG metadata + pin status; run/EvidenceCase
materialization importing `axor_eval` scorers; SSE event feed (Last-Event-ID replay,
sticky-session assumptions documented).

**3b. Replay API.** Scrubber data (state snapshot per cursor via kernel fold), counterfactual
endpoint (edited config/capabilities/taint → first-divergence result, hypothetical tail
flagged), regression-corpus run (auto-pin must-block on EvidenceCase, manual must-pass pins,
decision #11) producing exactly the report the `regression-report.jsx` mockup renders.

**3c. Plane service** (protocol v0.2): desired-state store (versioned, LWW, `stopped`
absorbing), command POST with per-operator ed25519 signatures (JCS canonical form + test
vectors), facts path (attestation append-only), LISTEN/NOTIFY → SSE fan-out, snapshot on
(re)subscribe, telemetry ingest with Idempotency-Key dedupe, heartbeat/stale tracking,
desired-vs-reported divergence surfaced.

**3d. GraphStore spike (do this before the layout is fixed — loose end 4):** confirm Kùzu's
single-writer-per-tenant model fits the ingest path. Mitigation if it doesn't: one writer
task per tenant DB behind an in-process queue (readers unaffected). Then implement
`GraphStore` (k-hop neighborhood cut, hottest-branches lens, attestation events as
first-class nodes) with `axor_sentinel` semantics.

Exit: proxy uploads traces; UI-facing APIs exist for every Phase 4 screen; signed pause/stop
round-trips against a stub adapter.

### Phase 4 — Frontend MVP (~3 weeks, overlaps 3)

Build order = funnel order; each mockup is the acceptance sketch, `expert-view-reference.jsx`
stays an explicit later opt-in, never default:

1. Shell + **Eval tab** (`main-tabs-eval-control-replay.jsx`): receipt-first screen, folds
   for trace/taint/health; live colour-coded stream over SSE. MVP cut per decision #4
   (no influence ranking, no CI-fenced experimental scores).
2. **Onboarding** (`onboarding.jsx`): tools → point agent → connection check; MCP import
   auto-fill; optional baseline health check as step 4.
3. **Replay tab**: virtualized event list scrubber (TanStack Virtual), step diff,
   counterfactual fork UI with first-divergence banner + hypothetical styling.
4. **Demo landing** (`demo-landing.jsx`): static site, recorded trace as JSON asset,
   TS rendering only (verdicts precomputed) — deployable the moment Phase 2's recorded
   trace exists; it is the lead asset, don't gate it on the rest.
5. **Control tab**: quiet topology list, per-node actions (pause/resume/stop/replan/inject/
   attest) with reason fields, desired-vs-reported ("applying…" until heartbeat confirms),
   test-bench gating for injection, greyed-with-label availability ladder (§12.4).
6. **Config Builder** (`config-builder.jsx`): manual sinks + criticality + per-arg allowlists,
   plain-language preview, "anything not listed is denied" confirmation, config emit.
   Per decision §5.2, the **generic wrapper scaffold ships first** (any hand-rolled agent:
   wrap the entry point in `Invokable`, tools declared as sinks) — framework-specific
   codegen (axor-langchain) is the optimization on top, not the prerequisite. Manual sink
   declaration is always available, so no agent is ever blocked on detection support.
   Code-in/wrapped-out (§11.3) can trail by a milestone — detection service + `axor wrap` CLI;
   detection v1 covers LangChain `@tool` + MCP manifests, everything else falls back to the
   generic scaffold + instructions (never guessing, per §11.3 hard rules).
7. **Health / self-heal panel** (`health-selfheal.jsx`): one-shot verdict, explicit-only heal
   with reason, heal→re-probe rendered as one unit, failure honesty.
8. **Regression report** (`regression-report.jsx`): corpus vs config v2, regressed rows open
   in replay at divergence.

Cross-cutting: TS types generated from kernel schemas (no hand-maintained mirrors);
greyed-panel convention ("available with adapter") everywhere; `intervened` badge fencing.

### Phase 5 — Adapter-side integration in the ecosystem (~2–3 weeks)

Work lands in ecosystem repos, platform consumes releases (dependency direction stays one-way):

- **axor-core: plane client.** Outbound-only SSE subscribe + telemetry batch POST with
  disk-backed queue; desired-state application at IntentLoop boundary only; lattice rules
  local (`stopped` absorbing, budget narrowing-only **enforced adapter-side**); injection
  at-most-once by id with consumed-id set; ed25519 verification with operator pubkeys from
  local config only; `hold_on_disconnect` option; heartbeat payload.
- **axor-probe: self-heal → context excision.** `repair/` gains the excision mechanism with
  the kernel's provenance guard; heal command arrives as signed plane command; auto re-probe
  of affected families; refusal diagnosis when drift attributes to operator-config segments.
- **axor-sentinel: attestation.** `operator_attestation` facts (append-only, revocation as
  new event), heat recompute over coverage, branch-scoped with node-wide as visible sugar;
  storage through the platform `GraphStore`.

Exit: the governed/ungoverned split-screen (§7A) runs live: same fault, denial vs
fabrication, one timeline — the strongest demo in the product.

### Phase 6 — Post-MVP (ordered backlog, not scheduled)

1. Notifications §16 (webhook emitter on the plane feed; dead-letter log in settings).
2. EvidenceCase export/share §8.3 (PDF + revocable permalink; observations only).
3. Budget caps UI (§15) — kernel/config work mostly done in Phases 1–2.
4. Expert view as opt-in mode (`expert-view-reference.jsx`).
5. Monetization scaffolding: `/ee` directory + Ed25519 license file check (reuses protocol §6
   crypto); pricing page. Line 1 (safety) stays free — enforced by the doc, checked at review.
6. Hosted deploy (Fly.io/Railway-class, sticky sessions); Kùzu-hosted tenancy layout.
7. Future scope §14: A2A, Key Vault — each re-opens the trust story; separate specs first.

---

## 3. Milestone → demo mapping (what is showable when)

| After phase | You can show |
|---|---|
| 1 | Golden-trace replay: recorded run re-folded bit-identically (the paper's core claim, testable) |
| 2 | Live agent through proxy, fault injected, EvidenceCase receipt produced |
| 2 + landing | Public demo page: recorded attack replay, zero infra |
| 3+4 (partial) | Full Eval tab + onboarding on hosted; replay scrubber over real traces |
| 5 | Governed vs ungoverned split-screen; signed live pause from the Control tab |

---

## 4. Risks, named

- **Kernel port purity.** The gate pipeline is currently entangled with runtime objects
  (governor, session state). If extraction stalls, resist the shortcut of a second
  implementation — that kills Rule 0 and counterfactual honesty with it. Timebox the port,
  shrink scope (fewer gates in kernel v1) rather than fork logic.
- **Module collision in `axor_core.kernel`.** Existing adjudicator/decidability/registration
  must be reconciled by layout decision *before* code moves (Phase 1 step 1).
- **Kùzu writer model** (loose end 4). Spike before layout commitment; fallback is a
  per-tenant writer queue, not a different database.
- **Solo bandwidth.** The proxy path (Phases 0–2 + landing) is a complete, shippable product
  slice on its own. If anything slips, cut from the bottom of Phase 4, never from Phase 1
  exit criteria.
- **Scope magnetism of the plane.** §12 write-actions beyond the fixed six each widen the
  observe/intervene boundary; the spec says justify individually — hold that line.

---

## 5. Decisions log (resolved 2026-07-05, operator)

Former open questions. Format mirrors the spec's §10: decision → consequence in the plan.

| # | Question | Decision |
|---|---|---|
| 1 | axor-core merge & release cadence | **Minimally invasive.** Additive-only merge (no renames, no API breaks, existing axor-core tests pass unmodified); gate pipeline extracted via delegation shims, gates that resist clean extraction stay in the runtime and are listed as not-yet-replayable instead of being force-restructured. No mid-plan PyPI release — platform pins a git ref until the next natural release. (Phase 1) |
| 2 | Custom-agent connectivity | **Any custom agent must be connectable — launch requirement.** Proxy path stays fully framework-agnostic (URL swap, no SDK, agent need not be Python). Adapter path: generic `Invokable` wrapper scaffold ships first; framework codegen is an optimization on top; manual sink declaration always available. (Phases 2, 4) |
| 3 | Protocol canonicalization | **Keep as planned: JCS (RFC 8785)** + committed test-vector file. (Phases 0, 3c) |
| 4 | axor-daemon / axor-classifier-simple | **Out of scope for now.** External ecosystem packages; no platform work items; daemon-executed tools get no special story in topology/trace views in v1. (§6) |
| 5 | Demo landing hosting | Undecided by operator → plan default stands: **separate static deploy target**, per architecture §7 ("static, zero infra"). Cheap to revisit. (Phase 4) |

---

## 6. Out of scope of this plan

- Everything in spec §14 (A2A end-to-end, Key Vault) beyond keeping boundaries adapter-thin.
- Live fork (§13.3), continuous drift monitoring, team features/SSO/RBAC (monetization doc
  Line 2) — scaffolding only in Phase 6.
- axor-daemon and axor-classifier-simple development (independent ecosystem packages).

---

## 7. Execution log (2026-07-05)

Executed in one pass, all branches `claude/design-mockups-plan-jgqwup`:

| Phase | Status | Proof |
|---|---|---|
| 0 Bootstrap | done | skeleton imported; kernel tests first-ever run; CI; spec v0.14 rename; protocol v0.2 (`pending_excision`, JCS, T=10s) |
| 1 Kernel | done | `axor_core.kernel.{events,state,degradation,replay}` additive; golden-trace zero-divergence test; counterfactuals (no-capability, synthetic taint, excision, budget); purity contracts; axor-core suite 912 passed unmodified |
| 2 Proxy | done | passthrough + axor-eval fault engine + mock tools + EvidenceCase; trace folds through kernel replay (rule 0 e2e test); 8 tests |
| 3 Backend | done | plane service (signed commands, SSE, facts), replay/regression APIs, Kuzu GraphStore (loose end 4 resolved: per-tenant single writer behind a lock); 14 tests |
| 4 Frontend | done | all seven mockups live over the real API; strict tsc + vite build green; E2E smoke with screenshots (eval receipt, replay fork, control round-trip, regression report) |
| 5 Adapter | mostly done | axor-core PlaneSession/PlaneClient (sig-verify, lattice, narrowing, one-shots, provenance guard); axor-probe excision shapes + heal→re-probe unit; axor-sentinel attestation recompute. **Remaining:** wiring PlaneSession polling into GovernedSession/IntentLoop and emitting kernel events from TraceCollector (runtime adoption), Sentinel cycle reading `effective_score` |

Deferred to Phase 6 (unchanged): notifications, EvidenceCase export/share, expert
view, /ee scaffolding, hosted deploy.
