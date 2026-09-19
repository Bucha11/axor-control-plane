# axor-control-plane

**Your agent lies when its tools fail. Catch it, then govern it.**

Break one of your agent's tools on purpose — a timeout, a poisoned result —
and most agents don't report the failure. They **fabricate**: "Based on the
search results, rates rose 0.25%." Zero bytes came back. Axor catches that
discrepancy, reconstructs how it happened, and turns it into a deterministic
regression test.

![Axor: run a fault scenario, catch the fabrication as an EvidenceCase, replay its taint graph, then spawn a live governed node](docs/demo.gif)

*One demo run: the agent fabricates a tool result under a deprived `web_search`; Axor catches the discrepancy as an **EvidenceCase**, replays its taint graph, and Control shows a real `axor_core`-governed node live.*

## The loop

The **EvidenceCase** is the artifact — a reproducible caught discrepancy
(observed reality vs. the agent's claim), not a score. Everything else is built
around producing, explaining, and preventing it.

- **Catch** — an observe-only proxy fronts your tools (auth passes through
  byte-for-byte, raw bodies never stored). Inject a fault; the audit compares
  what actually happened against what the agent claimed. The mismatch becomes an
  EvidenceCase — auto-uploaded, shareable and exportable (revocable link · HTML
  · PDF).
- **Explain** — scrub any run and fork counterfactuals ("no exec capability",
  "this value arrives tainted", "budget cap = N") that re-gate the recorded
  trace deterministically and show the first divergence. No model call, ever. A
  provenance graph draws each value's derivation; an edge links to the run it
  came from.
- **Prevent** — pin runs into a corpus (must-block auto-pins on evidence,
  must-pass by hand) and replay it under a candidate config: two-sided,
  deterministic CI. Change the policy, not the evidence.
- **Govern live** *(adapter depth)* — wrap your agent as an `axor_core`
  Invokable and it becomes a live node you can pause / stop / replan / inject /
  attest / budget-cap, with cascade-stop over a subtree. Operator commands are
  Ed25519-signed; Axor down ⇒ agent unaffected (advisory overlay).

## Depth ladder — and what each depth can honestly prove

**demo** (our mock broken tools) → **proxy** (your tools, observe-only) →
**adapter** (`axor_core`-governed, unlocks Control). Each rung is sold by value
already seen; nothing is pushed. In-app shortcuts light up the deep surfaces
without wiring an agent: `load example adapter run` (Replay), `load example
corpus` (Regression), `Spawn a governed demo node` (Control).

The honest version of "what do I actually get" per rung:

| Depth | Axor sees | Can prove | Can control |
|---|---|---|---|
| **demo** | our scripted agent + mock tools | a full EvidenceCase, end to end | — (showcase) |
| **proxy** | your tools' I/O | tool faults immediately; a full EvidenceCase once the agent's final claim is submitted (a one-line hook — see `examples/`) | — (observe-only) |
| **adapter** | agent trajectory + per-value provenance | full causal reconstruction | pause / stop / replan / cascade — the live Control plane |

The proxy is genuinely observe-only: it sees the tool boundary. Matching a tool
result against the agent's later *claim* needs that claim — the demo's scripted
agent submits it for you; on your own agent an `examples/` recipe does it in a
few lines. We call this out rather than imply a transparent proxy magically
governs a running agent.

## Try it on your own agent, without touching it

`axor-proxy` is on PyPI. No backend, no frontend, no database — the proxy alone
is enough to catch an agent claiming a tool worked when it did not:

```
pip install axor-proxy

axor-proxy --demo &                       # observe-only proxy on :8401
axor-proxy run --fault web_search:silent_fail -- your-agent "what did rates do?"
```

```
[axor] run run_cc25f3d0 armed (1 fault(s)); running agent…
Based on the search results, rates rose 0.25%.
[axor] ⚠ caught 1 discrepancy(ies) (deterministic)
       (claim reconstructed from observed tool calls) — 1 EvidenceCase(s)
```

`run` arms a run, executes your command untouched (streaming its output
through), and submits its final answer as the **claim** — the one thing an
observe-only proxy structurally cannot see, because it routes tool traffic and
nothing else. No claim, no EvidenceCase: the case *is* the contradiction between
what the tools did and what the agent said about them.

Point it at a backend to keep the runs: `--backend-url http://127.0.0.1:8400`.

**Which way to submit the claim.** There are three, and they trade code changes
against precision:

| | changes your code | how the claim is obtained | precision |
|---|---|---|---|
| adapter (`axor_wrap.WrappedToolset`, `GovernedSession`) | integration | the kernel drives the loop | exact, and can *enforce* |
| in-code hook (`examples/axor_hook.py`) | a few lines | knows each call's outcome | exact |
| `axor-proxy run` | **nothing** | reconstructed from observed calls | approximate |

`run` sees *that* several tools were called, not *which one* the answer leaned
on, so a confident answer after one of several tools faulted may be
over-attributed — a false positive. Use it to evaluate; use the hook or the
adapter for multi-tool or high-stakes agents. Use `run` **or** the hook, never
both: either one submits the claim.

> **Renamed in 0.2.0:** this subcommand was `axor-proxy wrap`. It is `run` now,
> with no alias — "wrap" in this codebase means what `axor_wrap.WrappedToolset`
> does to tool callables (gate, taint, verdict), and this command does none of
> that. `axor-proxy wrap` exits 2 with `unrecognized arguments`.

## Run it (Docker Compose)

The whole stack — postgres + backend + identity + observe-only proxy + frontend
— behind a single origin:

```
cp .env.example .env          # set AXOR_PG_PASSWORD; GITHUB_TOKEN to build private deps
GITHUB_TOKEN=ghp_… docker compose up --build
```

Open **http://localhost:8080** and click **Run demo-mode** (mock tools, zero
credentials). The frontend reverse-proxies `/v1` → backend (`:8400`), `/axor` →
proxy (`:8401`) and `/identity` → the login service (`:8402`), so the browser
talks to one origin; the proxy starts in demo-mode and auto-uploads runs to the
backend. For a real deployment set `AXOR_OPERATOR_KEYS`,
`AXOR_ALLOW_UNSIGNED=0`, `AXOR_PROXY_TOKEN` and a stable
`AXOR_IDENTITY_SIGNING_KEY` (see `.env.example`); the `GITHUB_TOKEN` is
build-only (a BuildKit secret), never lands in an image layer, and is optional
in a build network that already reaches the dependency sources.

## Dev (without containers)

```
uv sync --all-packages                   # workspace install
uv run pytest                            # kernel + platform tests
uv run scripts/gen_ts_types.py           # schema -> frontend/src/generated
AXOR_ALLOW_UNSIGNED=1 uv run uvicorn axor_backend.main:app --factory --port 8400 &
uv run axor-proxy --demo --backend-url http://127.0.0.1:8400 &
cd frontend && pnpm i && pnpm dev        # http://localhost:5173
```

Vite proxies `/v1` → `:8400` and `/axor` → `:8401`; it does **not** proxy
`/identity`, so human login is off in this loop — the operator master token and
scoped API keys are what Settings expects. To develop against login too, add
the identity service and point both ends at it:

```
AXOR_IDENTITY_PORT=8402 uv run axor-identity &       # it binds :8081 by default
#  backend:  AXOR_IDENTITY_JWKS_URL=http://127.0.0.1:8402/.well-known/jwks.json
#  frontend: VITE_IDENTITY_URL=http://127.0.0.1:8402
```

## What's in the monorepo

| Package | What | Rule that shapes it |
|---|---|---|
| `packages/axor-proxy` | Observe-only tool proxy + a demo governed node (real `axor_core` IntentLoop) | Auth passthrough byte-for-byte; two intervention points only (fault, observation) |
| `packages/axor-backend` | FastAPI: ingest, plane service (SSE+POST), replay/regression, taint graph, provenance, notifications, vault, Lab import/export, share/export, auth, EE license | Backend persists and fans out; it never interprets governance — that's the kernel's |
| `packages/axor-identity` | Human login: users, orgs, memberships, sessions; issues EdDSA access tokens + revocable refresh tokens, publishes a JWKS | Standalone — its own app, database and signing key. The backend and the Lab *verify* its tokens locally; nobody calls it per request |
| `frontend/` | React + TS (Zustand, TanStack Query, hash router; SVG taint graph) | quiet-until-wrong; TS types generated from the kernel event schema |

Supporting surfaces the loop rests on: **Config Builder** (declare
sinks/policies → a replayable config; budgets are call/cost caps enforced at the
loop boundary, in replay parity), **Notifications** (webhook on
`level_transition_up` / `heat_threshold` / `evidence_run` / `node_stale` /
`regression_failed` / `behavioral_drift` / `license_expiring`, with retries +
dead-letter), **Vault** (tool credentials dispensed at the sink so the agent
never holds them, and a separate signing vault behind its own credential; mint
and seal locally with `axor-proxy vault keygen` / `seal`), and **Auth** — three
credentials that compose:

- an opt-in **master token** (`AXOR_API_TOKEN`), the only all-scope principal;
- **scoped API keys**, `read` · `ingest` · `operate` · `admin`. Least-privilege
  by name, not a ladder: a higher scope does **not** imply a lower one, so an
  `admin` key does not read. A key may also be bound to one node, which is what
  stops an ingest key forging its neighbour's telemetry;
- **human login** via `axor-identity` — the backend verifies its access tokens
  against the published JWKS and maps the token's `org` + `role` onto scopes and
  a tenant, so a logged-in request only ever sees its own organization's data.

EE licensing is offline Ed25519 verification against an operator-pinned vendor
key (`AXOR_VENDOR_PUBKEY`); a key supplied in the request is refused.

## Running a tree of agents?

The lie travels: one bad tool call at a leaf becomes the orchestrator's
confident answer three hops later. Multi-agent Axor carries taint across every
hop, contains the fabrication at the boundary, and anchors **one** EvidenceCase
at the consequence with the causal subgraph that produced it — origin, conduit,
container. Foreign agents under someone else's keys are declared like sinks and
can't launder taint into your tree.

→ **[docs/multiagent.md](docs/multiagent.md)** for the full model (topology
lens, containment two-tree view, real governed tree, federation peers, vault).

## Ecosystem boundary

Existing PyPI packages are **external dependencies**, never workspace members:

| Package | Role here |
|---|---|
| `axor-core` | enforcement runtime. The platform imports the pure `axor_core.kernel` for replay, degradation and canonical (RFC 8785) bytes, plus `axor_core.policy` and `axor_core.contracts` on the Lab export/import path — a copy of a decoder is how a copy of a decision starts. The declared range is held to the *installed* kernel by a contract test (`packages/axor-proxy/tests/test_core_compatibility.py`), not by packaging |
| `axor-wrap` | the runtime that wraps tool callables — `WrappedToolset` (gate, taint, verdict) and `GovernedSession`, which is what "adapter depth" means. Via the `plane` extra it also carries the control-plane client the kernel no longer ships: the proxy's governed node connects with `axor_wrap.plane` (`PlaneClient` / `PlaneSession` / `PlaneAdmission`), and value refs are minted by its per-trace ledger (`axor_wrap.trace`) |
| `axor-eval` | scenario catalog + scoring — the proxy interprets its declarative scenario specs, the backend imports its scorers |
| `axor-probe` | behavioral drift: the node runs a battery and posts `health_payload` to `/v1/plane/{node}/probe-report`; the Health panel renders it. Imported, not restated — `axor_probe.integration.plane` *is* that door's vocabulary, and `axor_probe.integration.eval` re-keys the payload for grading. axor-probe and axor-eval may not import each other, so this backend is the only component that can hold the wire between them. The graded case stays out of every Eval score on purpose (ui-spec §8.2): derived on read beside the health report, never stored as run evidence |
| `axor-sentinel` | attestation semantics — append-only, revocation-as-an-event, same-keyset revocation, the required reason. Imported (`axor_sentinel.sentinel.attestation`), not restated. The cross-session reputation graph stays Sentinel's; the plane has no graph of its own, and reads its output as signed facts |

Dependency direction is one-way: ecosystem -> never depends on -> platform. Cost accepted: the backend image carries axor-core's full dependency tree.

Licensing: Apache-2.0, except `packages/axor-backend/src/axor_backend/ee/` (source-visible, commercial — see its `LICENSE`). Security: threat model + disclosure in `SECURITY.md`.

Specs: `docs/` — UI v0.14 · **spec v2 (multi-agent)** · architecture v0.1 · control-plane protocol v0.3 · monetization v0.1 · implementation plans v0.1 / **v2** · launch readiness v0.1. Mockups: `mockups/` (+ `mockups/v2/`). Cross-side signing vectors: `test-vectors/jcs-signing.json`.
