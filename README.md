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

The whole stack — postgres + identity + backend + observe-only proxy + frontend
— behind a single origin. **No GitHub account, no token, no login:**

```
git clone https://github.com/Bucha11/axor-control-plane && cd axor-control-plane
cp .env.example .env          # set AXOR_PG_PASSWORD — the only required value
docker compose up --build
```

Prefer not to build? The same images are published to GHCR on every release
(`:latest`) and every push to main (`:edge`):

```
cp .env.example .env
docker compose pull && docker compose up -d --no-build
```

`AXOR_TAG` in `.env` picks between them and defaults to `latest`. `latest`
tracks the newest `vX.Y.Z` tag, so between releases it is *older* than this
checkout — set `AXOR_TAG=edge` to run what main is at, or build from the clone
above, which matches it by construction. What each tag actually points at:

```
docker buildx imagetools inspect ghcr.io/bucha11/axor-platform:latest
```

Open **http://localhost:8080** and click **Run demo-mode** (mock tools, zero
credentials). The frontend reverse-proxies `/v1` → backend and `/axor` → proxy,
so the browser talks to one origin; the proxy starts in demo-mode and
auto-uploads runs to the backend. For a real deployment set
`AXOR_OPERATOR_KEYS` and `AXOR_ALLOW_UNSIGNED=0` (see `.env.example`).

The build once wanted a `GITHUB_TOKEN`, back when the ecosystem deps were
private git refs. They are published PyPI releases now — `uv.lock` holds zero
git sources — and the token plumbing that outlived them is gone from the
Dockerfile, compose and CI. A test keeps it gone
(`test_quickstart_is_credential_free.py`): nothing on the path from `git clone`
to a running stack may require an account.

## Dev (without containers)

```
uv sync --all-packages                   # workspace install
uv run pytest                            # kernel + platform tests
uv run scripts/gen_ts_types.py           # schema -> frontend/src/generated
AXOR_ALLOW_UNSIGNED=1 uv run uvicorn axor_backend.main:app --factory --port 8400 &
uv run axor-proxy --demo --backend-url http://127.0.0.1:8400 &
cd frontend && pnpm i && pnpm dev        # http://localhost:5173
```

## What's in the monorepo

| Package | What | Rule that shapes it |
|---|---|---|
| `packages/axor-proxy` | Observe-only tool proxy + a demo governed node (real `axor_core` IntentLoop) | Auth passthrough byte-for-byte; two intervention points only (fault, observation) |
| `packages/axor-backend` | FastAPI: ingest, plane service (SSE+POST), replay/regression, taint graph, notifications, share/export, auth, EE license | Backend persists and fans out; it never interprets governance — that's the kernel's |
| `frontend/` | React + TS (Zustand, TanStack Query, hash router; SVG taint graph) | quiet-until-wrong; TS types generated from the kernel event schema |

Supporting surfaces the loop rests on: **Config Builder** (declare
sinks/policies → a replayable config; budgets are call/cost caps enforced at the
loop boundary, in replay parity), **Notifications** (webhook on level-up /
heat-threshold / evidence-run / node-stale, with retries + dead-letter), and
**Auth** (opt-in master token + scoped API keys `read < ingest < operate <
admin`; EE license via offline Ed25519 verification).

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
| `axor-core` | enforcement runtime; the platform imports its pure submodule `axor_core.kernel` for replay (purity guarded by a contract test, not packaging) |
| `axor-eval` | scenario catalog + scoring — the proxy interprets its declarative scenario specs, the backend imports its scorers |
| `axor-probe` | behavioral drift: the node runs a battery and posts `health_payload` to `/v1/plane/{node}/probe-report`; the Health panel renders it. Not imported here — the payload shape is the whole contract. Kept out of every Eval score on purpose (ui-spec 8.2) |
| `axor-sentinel` | attestation semantics — append-only, revocation-as-an-event, same-keyset revocation, the required reason. Imported (`axor_sentinel.sentinel.attestation`), not restated. The cross-session reputation graph stays Sentinel's; the plane has no graph of its own, and reads its output as signed facts |

Dependency direction is one-way: ecosystem -> never depends on -> platform. Cost accepted: the backend image carries axor-core's full dependency tree.

Licensing: Apache-2.0, except `packages/axor-backend/src/axor_backend/ee/` (source-visible, commercial — see its `LICENSE`). Security: threat model + disclosure in `SECURITY.md`.

Specs: `docs/` — UI v0.14 · **spec v2 (multi-agent)** · architecture v0.1 · control-plane protocol v0.3 · monetization v0.1 · implementation plans v0.1 / **v2** · launch readiness v0.1. Mockups: `mockups/` (+ `mockups/v2/`). Cross-side signing vectors: `test-vectors/jcs-signing.json`.
