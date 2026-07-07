# axor-control-plane

Runtime governance and evaluation platform for LLM agents. Monorepo.

![Axor: run a fault scenario, catch the fabrication as an EvidenceCase, replay its taint graph, then spawn a live governed node](docs/demo.gif)

*One demo run: the agent fabricates a tool result under a deprived `web_search`; governed Axor catches the discrepancy as an EvidenceCase, and Control shows a real `axor_core`-governed node live.*

Naming note: "control plane" is both this platform and one of its subsystems
(spec section 12 — the SSE+POST advisory channel). In code and docs the
subsystem is always called the **plane service** (`axor_backend.plane`);
"control plane" unqualified means the platform.

| Package | What | Rule that shapes it |
|---|---|---|
| `packages/axor-proxy` | Observe-only tool proxy + a demo governed node (real `axor_core` IntentLoop) | Auth passthrough byte-for-byte; two intervention points only (fault, observation) |
| `packages/axor-backend` | FastAPI: ingest, plane service (SSE+POST), replay/regression, taint graph, notifications, share/export, auth, EE license | Backend persists and fans out; it never interprets governance — that's the kernel's |
| `frontend/` | React + TS (Zustand, TanStack Query, hash router; SVG taint graph) | quiet-until-wrong; TS types generated from the kernel event schema |

## What it does

- **Eval** — run a fault scenario through the proxy (or one click with the built-in scripted agent). The caught discrepancy is an **EvidenceCase** (observed reality vs the agent's claim), auto-uploaded, shareable and exportable (revocable link · HTML · PDF).
- **Replay** — scrub any run; fork counterfactuals ("no exec capability", "this value arrives tainted", "budget cap = N") that re-gate the recorded trace deterministically and show the first divergence. A provenance graph draws each value's derivation; an edge links to the run it came from.
- **Control** (adapter depth) — live topology of governed nodes: per-node pause / stop / replan / inject / attest / budget-cap and cascade-stop over a subtree. **Spawn a real governed node** (`axor_core` IntentLoop) from the UI to see it heartbeat and obey interventions.
- **Regression** — pin runs (must-block auto-pins on evidence, must-pass by hand) and replay the corpus under a candidate config: two-sided, deterministic CI.
- **Config Builder** — declare sinks/policies → a replayable config. Budgets are call/cost caps (per-tool weights) enforced at the loop boundary, in replay parity (§15).
- **Notifications** — webhook on level-up / heat-threshold / evidence-run / node-stale, with retries + dead-letter.
- **Auth** (opt-in) — master token + scoped API keys (`read < ingest < operate < admin`). **EE license** — offline Ed25519 verification.

Depth ladder: **demo** (mock tools) → **proxy** (your tools, observe-only) → **adapter** (`axor_core`-governed, unlocks Control). In-app shortcuts light up the deep surfaces without wiring an agent: `load example adapter run` (Replay), `load example corpus` (Regression), `Spawn a governed demo node` (Control).

## Run it (Docker Compose)

The whole stack — postgres + backend + observe-only proxy + frontend — behind a
single origin:

```
cp .env.example .env          # set AXOR_PG_PASSWORD; GITHUB_TOKEN to build private deps
GITHUB_TOKEN=ghp_… docker compose up --build
```

Open **http://localhost:8080**. The frontend reverse-proxies `/v1` → backend and
`/axor` → proxy, so the browser talks to one origin; the proxy starts in
demo-mode (mock tools) and auto-uploads runs to the backend. The taint graph is a
derived index rebuilt from the persisted event log at boot (durable across
restarts, no graph DB; embedded Kùzu is available per-tenant for hosted). For a
real deployment set `AXOR_OPERATOR_KEYS` and
`AXOR_ALLOW_UNSIGNED=0` (see `.env.example`); the `GITHUB_TOKEN` is build-only
(a BuildKit secret) and never lands in an image layer.

## Dev (without containers)

```
uv sync --all-packages                   # workspace install
uv run pytest                            # kernel + platform tests
uv run scripts/gen_ts_types.py           # schema -> frontend/src/generated
AXOR_ALLOW_UNSIGNED=1 uv run uvicorn axor_backend.main:app --factory --port 8400 &
uv run axor-proxy --demo --backend-url http://127.0.0.1:8400 &
cd frontend && pnpm i && pnpm dev        # http://localhost:5173
```

## Ecosystem boundary

Existing PyPI packages are **external dependencies**, never workspace members:

| Package | Role here |
|---|---|
| `axor-core` | enforcement runtime; the platform imports its pure submodule `axor_core.kernel` for replay (purity guarded by a contract test, not packaging) |
| `axor-eval` | scenario catalog + scoring — the proxy interprets its declarative scenario specs, the backend imports its scorers |
| `axor-probe` | health-check verdicts surfaced on the Eval tab |
| `axor-sentinel` | cross-session graph semantics; GraphStore here is its storage face |

Dependency direction is one-way: ecosystem -> never depends on -> platform. Cost accepted: the backend image carries axor-core's full dependency tree.

Licensing: Apache-2.0, except `packages/axor-backend/src/axor_backend/ee/` (source-visible, commercial — see its `LICENSE`).

Specs: `docs/` — UI v0.14 · architecture v0.1 · control-plane protocol v0.2 · monetization v0.1 · implementation plan v0.1. Mockups: `mockups/`.
