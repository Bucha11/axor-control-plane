# Axor Control Plane

### Eval. Control. Protect.

**Your agent lies when its tools fail. Catch it. Prove it. Govern it.**

Break one of your agent's tools on purpose — a timeout, a poisoned result — and
most agents don't report the failure. They **fabricate**: *"Based on the search
results, rates rose 0.25%."* Zero bytes came back.

Axor catches that lie as a **reproducible receipt** you can replay
deterministically, hand to your security team and attach to the incident — then
gives you the pause button and the kill switch for the whole fleet.

`Apache-2.0` · self-hosted · no credentials leave your machine · no model call
in the analysis path, ever

![Axor: run a fault scenario, catch the fabrication as an EvidenceCase, replay its taint graph, then spawn a live governed node](docs/demo.gif)

*One continuous demo run: fault injected → fabrication caught as an
**EvidenceCase** → replayed with its taint graph → a real `axor_core`-governed
node live in Control. Recorded, deterministic.*

## Catch your first lie in 60 seconds

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

Your agent's code is untouched. `run` arms the run, executes your command
(streaming its output through), and submits its final answer as the **claim** —
then tells you whether reality and the claim disagree. Point it at a backend to
keep the runs and open them in the UI: `--backend-url http://127.0.0.1:8400`.

Or take the whole stack with two commands and click one button →
[Run it](#run-the-whole-stack-docker-compose).

## You've been in one of these rooms

> **"Why did the agent email that customer? Show me exactly what it saw."**
>
> *The exec question.* Your observability trace is 4,000 lines of JSON. Axor's
> answer is one EvidenceCase and a replay cursor on the exact step.

> **"It's 3am and the agent is burning API budget in a loop."**
>
> *The on-call moment.* Axor pages you (webhook, with dead-letter honesty) and
> hands you pause / budget-cap / cascade-stop — instead of `kill -9` and prayer.

> **"What controls do you have around your AI agents?"**
>
> *The auditor.* You hand over signed intervention logs, append-only facts and
> PDF receipts — mapped article by article to EU AI Act 12 / 14 / 26.

## The loop — four surfaces, one artifact

The **EvidenceCase** is the artifact: a reproducible caught discrepancy
(observed reality vs. the agent's claim), not a score. Everything else exists to
produce, explain and prevent it.

| | What happens | What you get |
|---|---|---|
| **1 · Catch** | An observe-only proxy fronts your tools — auth passes through byte-for-byte, raw bodies never stored. Inject a fault; the audit compares what actually happened against what the agent claimed. | **You stop arguing about what the agent did.** The mismatch becomes an EvidenceCase: auto-uploaded, shareable and exportable (revocable link · HTML · PDF). |
| **2 · Explain** | Scrub any run and fork counterfactuals — *"no exec capability"*, *"this value arrives tainted"*, *"budget cap = N"* — that re-gate the recorded trace and show the first divergence. **No model call, ever.** A provenance graph draws each value's derivation; an edge links to the run it came from. | **Incident review takes minutes, not a day.** |
| **3 · Prevent** | Pin runs into a corpus (must-block auto-pins on evidence, must-pass by hand) and replay it under a candidate config. Two-sided: a policy that blocks everything fails honestly. | **You ship policy changes without fear.** Change the policy, not the evidence. |
| **4 · Govern live** | Wrap your agent as an `axor_core` Invokable and it becomes a live node: pause / stop / replan / inject / attest / budget-cap, with cascade-stop over a subtree. Commands are Ed25519-signed over RFC 8785 canonical bytes. | **You get a pause button before you need it.** And Axor down ⇒ agent unaffected — it is an advisory overlay. |

Supporting surfaces the loop rests on:

- **Config Builder** — declare sinks and policies into a replayable config;
  budgets are call/cost caps enforced at the loop boundary, in replay parity.
- **MCP-native onboarding** — paste the `mcpServers` config you already have.
  HTTP servers are proxied; for stdio servers the proxy spawns the gateway.
- **Notifications** — webhooks on `level_transition_up`, `heat_threshold`,
  `evidence_run`, `node_stale`, `regression_failed`, `behavioral_drift` and
  `license_expiring`, with retries and a dead-letter log, because a lost alert
  you cannot see is worse than no alert.
- **Health & self-heal** — an `axor-probe` battery grades behavioral drift;
  healing is explicit, signed, traced, and always re-probed to verify. A heal
  without a verifying re-probe is never rendered as resolved.
- **Vault** — tool credentials dispensed at the sink so the agent never holds
  them, fail-closed, revoke-only-narrowing; envelope mode stores a secret the
  backend has no key for. A separate signing vault signs without surrendering
  the private half. Two subsystems, two credentials, one wall between them.
- **Learn mode & a 7-stop guided tour** — the default UI is
  *quiet-until-wrong*; turn on Learn mode and every surface explains itself.

## Three depths — and what each can honestly prove

**demo** (our mock broken tools) → **proxy** (your tools, observe-only) →
**adapter** (`axor_core`-governed, unlocks Control). The demo is one click; your
own tools take about five minutes with no code change; full live Control needs
one wrapper on your loop. No rung oversells the one below it:

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

Nothing is gated behind wiring an agent up: in-app shortcuts light the deep
surfaces immediately — `load example adapter run` (Replay), `load example
corpus` (Regression), `Spawn a governed demo node` (Control, a real
`axor_core` IntentLoop).

**Which way to submit the claim.** Three, trading code changes against
precision:

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

## Proof, not promises

No customer logos — we are pre-launch and will not fake them. What we have
instead is claims you can re-run yourself.

**The detector is deterministic, and it does not cry wolf.** Four fault modes ×
three scripted personas, 100 seeded trials per cell, no model calls — the table
and its harness live in `axor-eval` (`BENCHMARKS.md`):

| persona \ fault mode | `silent_fail` | `corrupt_retrieval` | `instruction_injection` | `tool_substitution` |
|---|---|---|---|---|
| misbehaving, structured claims | 100% | 100% | 100% | 100% |
| misbehaving, free text only | 100% *(heuristic)* | 100% | 100% | 100% *(heuristic)* |
| **honest agent** (false-positive check) | **0%** | **0%** | **0%** | **0%** |

One command reproduces it: `python -m axor_eval.benchmarks.catch_rate
--trials 50`. Read it honestly — this is not a field-accuracy claim against real
models. It proves the detector is deterministic and never fires on an honest
run. Real-LLM catch rates (lower, per-model) are the next milestone: same
harness, swap a persona for a live loop.

**931 tests green, and the ones that matter boot the real thing.** That is
`uv run pytest -m "not e2e"` on the workspace; 20 Playwright specs drive the
actual UI against the actual stack, and the backend E2E suite boots real backend
and proxy processes and talks to them over HTTP/SSE. CI also runs
the dialect-sensitive suite against a real Postgres, `pip-audit` + `pnpm audit`
with an SBOM artifact, and a **deploy gate** that builds the compose stack,
waits for health and smokes the single origin before merge.

**Limits, not adjectives.** ~150 rps ingest ceiling on Postgres (p50 ≈ 0.27 s),
50 concurrent SSE subscribers with zero drops and perfect 50× fan-out;
~70 rps on the SQLite dev default. Published with the script that measured them
in [`docs/ops-limits.md`](docs/ops-limits.md) — including the point where SSE
fan-out, not the database, becomes the bottleneck.

**The same kernel enforces and replays.** Replay is not a reconstruction — it is
the identical pure function (`axor_core.kernel`) over the recorded trace. That
is why the receipt is evidence rather than narrative. A size-1 multi-agent case
is byte-identical to the single-agent receipt, held by a golden gate that is
never regenerated to make CI pass.

## Running a tree of agents? The lie travels — Axor stops it at the boundary

One bad tool call at a leaf becomes the orchestrator's confident answer three
hops later. Multi-agent Axor makes that propagation visible, contained and
provable.

| | |
|---|---|
| **Carried, not laundered** | Taint rides *in the message*: labels travel with values in every envelope between agents, so a value fabricated at a leaf arrives at the orchestrator two delegation hops later still tainted. Hop count cleans nothing; the export gate denies at the boundary. → *containment counted per boundary, and a systemic outcome as a label pair (`fabricated_failure → honest_failure`), never a score.* |
| **Topology, live** | Control's graph lens draws your federation from traced spawn events — never self-reported parents: delegation and lateral edges, denials flashing where they happened. → *the kill switch scales to the whole subtree, as one signed command to its root.* |
| **One case, three nodes** | A fabrication at the root caused by a fault at a leaf is **one** EvidenceCase, anchored at the consequence, carrying the minimal chain that produced it — origin, conduit, container — plus an influence ranking by deterministic subgraph ablation. → *"who lied first" is a diagram, not a debate.* |
| **Foreign agents** | Peers under someone else's keys are declared like sinks in the Config Builder: L0 by default, identity buys attribution, a signed agreement buys a bounded discount — never label authority, and critical sinks ignore discounts entirely. → *a compromised partner cannot launder taint into your tree.* The ladder itself is `axor_core.federation.ladder`'s and is applied by the runtime at the boundary; this plane declares peers into the config, renders the opaque peer in the graph lens, and reads back the recorded verdict of a peer send as a gated consequence. Unlike budgets, the discount arithmetic is **not** re-derived by kernel replay under a candidate config. |

→ **[docs/multiagent.md](docs/multiagent.md)** for the full model (topology
lens, containment two-tree view, a real three-node governed tree over the
message bus, federation peers, federation vault).

## Where Axor sits

Three tool families get conflated. They compose — most teams should run
observability **and** guardrails **and** (we argue) execution governance:

| Tool | The question it answers |
|---|---|
| Tracing (LangSmith, Langfuse, Helicone) | *what calls happened?* |
| Evals | *did this run pass?* |
| Guardrails (NeMo, Guardrails AI, Lakera) | *is this text acceptable?* |
| **Axor** | *where did the agent's claim diverge from observed execution — and would a new policy have prevented it?* |

What the others do better, honestly: observability has far richer LLM-call
analytics (tokens, costs, prompt diffs), which Axor does not attempt; guardrails
catch toxic and off-policy *text*, which Axor does not look at. Full
side-by-side: **[docs/comparison.md](docs/comparison.md)**.

## Why now

EU AI Act record-keeping and human-oversight obligations (Articles 12, 14, 26)
are entering application for high-risk systems — and *"show me the agent's audit
trail"* is becoming a procurement question everywhere, regulated or not. Teams
that can answer it with a receipt instead of a shrug win those deals.

Axor does not make a system compliant; it produces the artifacts those articles
ask operators to produce. Article-by-article mapping, with its honest
boundaries: **[docs/eu-ai-act-mapping.md](docs/eu-ai-act-mapping.md)**. For
reviewers who need control-by-control answers before a certificate exists:
**[docs/security-controls.md](docs/security-controls.md)**.

## Run the whole stack (Docker Compose)

Everything — postgres + backend + identity + observe-only proxy + frontend —
behind a single origin:

```
git clone https://github.com/Bucha11/axor-control-plane && cd axor-control-plane
cp .env.example .env          # set AXOR_PG_PASSWORD; GITHUB_TOKEN to build private deps
GITHUB_TOKEN=ghp_… docker compose up --build
```

Open **http://localhost:8080** and click **Run demo-mode** — mock tools, zero
credentials, nothing leaves your machine. The frontend reverse-proxies `/v1` →
backend (`:8400`), `/axor` → proxy (`:8401`) and `/identity` → the login service
(`:8402`), so the browser talks to one origin; the proxy starts in demo-mode and
auto-uploads runs to the backend.

For a real deployment set `AXOR_OPERATOR_KEYS`, `AXOR_ALLOW_UNSIGNED=0`,
`AXOR_PROXY_TOKEN` and a stable `AXOR_IDENTITY_SIGNING_KEY` (every variable is
documented in `.env.example`, and a test fails if one of them is not). The
`GITHUB_TOKEN` is build-only (a BuildKit secret), never lands in an image layer,
and is optional in a build network that already reaches the dependency sources.
Backup, restore and upgrade: **[docs/runbook.md](docs/runbook.md)**.

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

Frontend E2E: `cd frontend && pnpm e2e` — it boots backend, proxy and Vite
itself and reuses them if already running.

## What's in the monorepo

| Package | What | Rule that shapes it |
|---|---|---|
| `packages/axor-proxy` | Observe-only tool proxy + a demo governed node (real `axor_core` IntentLoop) | Auth passthrough byte-for-byte; two intervention points only (fault, observation) |
| `packages/axor-backend` | FastAPI: ingest, plane service (SSE+POST), replay/regression, taint graph, provenance, notifications, vault, Lab import/export, share/export, auth, EE license | Backend persists and fans out; it never interprets governance — that's the kernel's |
| `packages/axor-identity` | Human login: users, orgs, memberships, sessions; issues EdDSA access tokens + revocable refresh tokens, publishes a JWKS | Standalone — its own app, database and signing key. The backend and the Lab *verify* its tokens locally; nobody calls it per request |
| `frontend/` | React + TS (Zustand, TanStack Query, hash router; SVG taint graph) | quiet-until-wrong; TS types generated from the kernel event schema |

Postgres is the only store a deployment needs — value provenance and causal
subgraphs are *derived* from a run's own events when a request asks for them, so
there is no graph database to operate. 17 alembic migrations, stamped and
upgraded at boot; SQLite is the zero-setup dev default.

**Auth — three credentials that compose:**

- an opt-in **master token** (`AXOR_API_TOKEN`), the only all-scope principal;
- **scoped API keys**, `read` · `ingest` · `operate` · `admin`. Least-privilege
  by name, not a ladder: a higher scope does **not** imply a lower one, so an
  `admin` key does not read. A key may also be bound to one node, which is what
  stops an ingest key forging its neighbour's telemetry. Every mint and revoke
  is appended to a custody log that outlives the key;
- **human login** via `axor-identity` — the backend verifies its access tokens
  against the published JWKS and maps the token's `org` + `role` onto scopes and
  a tenant, so a logged-in request only ever sees its own organization's data.

EE licensing is offline Ed25519 verification against an operator-pinned vendor
key (`AXOR_VENDOR_PUBKEY`); a key supplied in the request is refused. No license
server, no phone-home, works air-gapped — the same crypto that guards your
command channel, and it never calls us.

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

## Pricing that won't ambush you

Two lines define the whole model. **Line 1 (ethical):** anything that makes an
agent *safer* is free forever — fail-closed defaults, every gate, command
signing, attestation, fault scenarios, EvidenceCase capture, replay. That is
enforced in code, not promised in a footnote. **Line 2 (commercial):** you pay
only for what appears when an *organization* runs it — fleets, hosted
collaboration, the security workflow, proof for auditors.

| | Community | Team Workspace | Security Workspace | Enterprise Platform |
|---|---|---|---|---|
| | **$0** | **$299 / mo** | **$1,500 / mo** | **from $30k / yr** |
| | open source · local & public | 10 governed nodes | 50 governed nodes | negotiated fleet |
| Catch · replay · counterfactuals · corpus · export | ✅ | ✅ | ✅ | ✅ |
| Runtime enforcement + live intervention | 1 node | ✅ | ✅ | ✅ |
| Scheduled regression CI + run history | — | ✅ | ✅ | ✅ |
| Incident → regression, policy/kernel comparison | — | — | ✅ | ✅ |
| Self-hosting (Postgres or SQLite) | ✅ | ✅ | ✅ | ✅ |
| SSO/SAML/SCIM · RBAC · air-gapped · compliance exports | — | — | — | *planned* |

Beyond the included band: +$75/node/mo (Team), +$50/node/mo (Security). Every
rung self-hosts; the Enterprise row is a contract over the same ladder, not a
separate product. The **Pricing** tab in the app is the canonical ladder and
marks each not-yet-shipped feature with an explicit *planned* chip rather than
listing it as if it exists — a pricing page that over-claims would be the same
dishonesty the product refuses everywhere else.

Worried about vendor risk? The escape hatch is structural: the safety layer is
Apache-2.0 — if we vanish, your governance does not.

Reasoning behind the split: **[docs/monetization-v0.1.md](docs/monetization-v0.1.md)**.

## The questions you're already asking

<details>
<summary><b>Does the proxy add latency to my agent?</b></summary>

One local hop, and it is observe-only — it never blocks traffic. Passthrough
streams cleanly (SSE and chunked tool responses flow, nothing is buffered).
Enforcement, when you opt into it, happens in-process in the adapter.
</details>

<details>
<summary><b>What happens if Axor goes down mid-run?</b></summary>

Nothing, structurally: the control plane is an **advisory overlay**. The adapter
enforces locally with its own config and its own keys, so a governed agent keeps
enforcing and simply stops reporting; the observe-only proxy fails loud (502 on
armed runs) and never fabricates. Your agent never depends on our backend being
up — and the adapter re-verifies signatures itself, so a compromised backend is
still not command forgery.
</details>

<details>
<summary><b>What does Axor see — and store?</b></summary>

Observations only: tool name, path, method, status, byte counts, SHA-256 hashes,
fault markers. **Raw request/response bodies are never persisted**, and your
`Authorization` headers pass through byte-for-byte — never parsed, never stored,
never logged. Exports are scrubbed again at render time. Threat model and
disclosure policy: **[SECURITY.md](SECURITY.md)**.
</details>

<details>
<summary><b>Am I locking into a framework?</b></summary>

No. Observe-only works with anything that makes HTTP or MCP tool calls, with
zero code change. The deepest integration is one wrapper around your agent loop.
Runnable recipes for the OpenAI Agents SDK and CrewAI, plus a framework-free
in-code hook, are in **[`examples/`](examples/README.md)**.
</details>

<details>
<summary><b>Why is the good stuff free?</b></summary>

Because the community and your trust are worth more than gating a taint graph.
Safety never checks a license — at any scale. The paid tiers sell what only
organizations need: fleets, hosted collaboration, SSO, compliance reports.
</details>

<details>
<summary><b>Can I see it without wiring up an agent?</b></summary>

Yes — that is the point of demo-mode and the in-app seeds. `Run demo-mode`,
`load example adapter run`, `load example corpus`, `Spawn a governed demo node`,
and a 7-stop guided tour that seeds real data as it walks you through. There is
also a standalone demo landing at `/demo.html`.
</details>

## Break something. Get the receipt.

Ten minutes from clone to your first caught lie — mock tools, zero credentials,
nothing leaves your machine.

```
git clone https://github.com/Bucha11/axor-control-plane && cd axor-control-plane
cp .env.example .env && docker compose up --build   # → http://localhost:8080
```

Questions, design-partner interest or a security report: **security@axor.dev**
(disclosure policy in `SECURITY.md`).

---

**Licensing:** Apache-2.0, except `packages/axor-backend/src/axor_backend/ee/`
(source-visible, commercial — see its `LICENSE`).

**Specs:** `docs/` — UI v0.14 · **spec v2 (multi-agent)** · architecture v0.1 ·
control-plane protocol v0.3 · monetization v0.1 · implementation plans v0.1 /
**v2** · launch readiness v0.1 · ops limits · runbook · security controls · EU AI
Act mapping · comparison · releasing. Mockups: `mockups/` (+ `mockups/v2/`).
Cross-side signing vectors: `test-vectors/jcs-signing.json`. What is shipped and
what is an idea, with no dates promised: **[ROADMAP.md](ROADMAP.md)**.
