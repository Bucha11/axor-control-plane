# Axor Control Plane — Launch Readiness (v0.1)

What must be finished, per direction, before the public launch (HN post + paper
preprint + PyPI/compose release). Grounded in the marketing read: the wedge is
the **EvidenceCase** (a shareable caught-lie receipt), the Team buyer is the
**SRE/platform engineer**, the Enterprise buyer arrives later via design
partners and the paper. Priorities: **P0** = launch blocker, **P1** = launch
week, **P2** = fast-follow (≤30 days after).

Status legend: ☐ open · ◐ partial exists · ☑ done.

## 1. Product — close the gaps that first contact will hit

| P | Item | Why / done-when | Status |
|---|---|---|---|
| P0 | **MCP-native onboarding** | Onboarding says "parsing a real MCP manifest is on the roadmap" — for the 2026 agent stack MCP *is* the tool layer. Done when: paste/upload an MCP manifest → tools declared, proxied MCP endpoint works against a real client. | ☑ (HTTP **and stdio** — the proxy spawns local `command:` servers as gateways; loopback-only registration) |
| P0 | **Streaming passthrough in the proxy** | `tool_route` buffers the full upstream body; SSE/chunked tool responses (LLM-backed tools) stall. Done when: pass-through streams, observation records size/hash without buffering. | ☑ |
| P0 | **DB migrations** | `create_all` only; first schema change after launch strands early adopters. Done when: alembic baseline + upgrade path tested SQLite+Postgres. | ☑ |
| P1 | **Concurrent runs per proxy** | One active run at a time (v1) breaks the first team that shares a proxy. Done when: N armed runs keyed by header/route, docs updated. | ☑ |
| P1 | **OpenAI Agents SDK + CrewAI adapters** | The Invokable wrap is axor-core-native; the two biggest agent frameworks need a 20-line published recipe each (full middleware later). Done when: `examples/` runs green in CI against both. | ◐ (recipes published in examples/; CI-green needs LLM keys — honest note in examples/README) |
| P1 | **Retention/rotation** | Unbounded events table + trace dir. Done when: `AXOR_RETENTION_DAYS` prunes runs + traces, documented. | ☑ |
| P2 | Hosted multi-tenancy — explicitly **not** for launch; self-host only. | | ◐ |

## 2. Security & trust posture — the first thing a security buyer greps

| P | Item | Done-when | Status |
|---|---|---|---|
| P0 | **SECURITY.md + threat model page** | Disclosure address, supported versions, and a one-page threat model: what the proxy sees, what is never stored (raw bodies), advisory-overlay failure mode ("Axor down ⇒ agent unaffected"). This is the #1 pre-sales objection — answer it in the repo. | ☑ |
| P0 | **Default-secure compose** | `.env.example` ships auth ON commented with one-liner to disable, not the reverse; CORS explicit; share-token entropy documented. | ☑ |
| P1 | **Dependency/SBOM + pip-audit in CI** | `pip-audit`/`npm audit` gate + published SBOM artifact. | ☑ |
| P2 | SOC2-lite page (controls narrative, no cert claim), DPA template for design partners. | | ☑ (docs/security-controls.md; full partner paperwork in docs/partner/ — agreement, NDA, DPA; counsel deferred to first paid/hosted deal) |

## 3. Distribution & DevRel — the launch itself

| P | Item | Done-when | Status |
|---|---|---|---|
| P0 | **Publish to PyPI**: `axor-proxy` (uvx path is quoted all over the UI/docs and currently 404s), `axor-backend`. Compose images to GHCR. | `uvx axor-proxy --demo` works on a clean machine. | ◐ **NOT published yet** — the packages are not on PyPI, so `uvx axor-proxy` still 404s. Everything up to the publish button is done and verified clean-room (both wheels build; install against PyPI-resolved core 0.9.2 / eval 0.1.0; `--demo` serves healthz; release.yml + **docs/RELEASING.md** ready). The publish itself is the remaining human-only step: register the two trusted publishers on pypi.org, then push tag v0.1.0. |
| P0 | **Public landing + docs site** | The in-app Home is not a website. Static site: hero = demo GIF (have) + "Run demo-mode" → hosted sandbox or 2-command local start; docs = quickstart, depth ladder, protocol, FAQ. | ◐ (site/ + Pages workflow ready; enable Pages + domain) |
| P0 | **Launch post** | "Your agent lies when its tools fail — here's the receipt": narrative + GIF + benchmark table (catch rates by fault mode). HN + r/LocalLLaMA + X thread. | ◐ (draft in docs/launch-post-draft.md; needs benchmark table) |
| P1 | **EvidenceCase link unfurl** | Share permalink gets OG tags + "Caught by Axor" footer — every shared receipt is an ad. (Revocability already done.) | ☑ |
| P1 | **Community surface**: CONTRIBUTING.md, issue templates, GH Discussions on, public ROADMAP.md (honest: hosted=later, SSO=Enterprise-later). | | ☑ (Discussions toggle needs repo admin) |
| P1 | **5-minute video**: demo → proxy on your tools → caught lie → replay. | | ◐ (shot list in docs/video-script.md; recording needs a human voice) |
| P2 | Comparison page ("vs observability, vs guardrails") — honest table, no FUD. | | ☑ (docs/comparison.md) |

## 4. Paper — the credibility engine

| P | Item | Done-when | Status |
|---|---|---|---|
| P0 | **Benchmark harness**: catch-rate table (fault mode × agent framework × model) using axor-eval scenarios; deterministic, scripted, in `axor-benchmarks`. | One command reproduces the table. | ☑ (lives in axor-eval `benchmarks/` — in-scope Apache-2.0 home; `--trials N --write` reproduces BENCHMARKS.md; per-model rows = swap a persona for an LLM loop) |
| P0 | **Artifact package**: ecosystem-only (Apache-2.0, per monetization §7 the artifact ships from ecosystem packages, not the platform), anonymized variant for double-blind. | | ◐ |
| P1 | Venue pick + submission calendar (target: workshop deadline first, main venue after). | | ☐ |

## 5. Monetization mechanics — able to take money on day 1

| P | Item | Done-when | Status |
|---|---|---|---|
| P0 | **Pricing page CTA → capture**: "Get Team" = email/checkout link (Stripe payment link is enough; invoicing by hand). Today the page is display-only. | A stranger can pay without talking to us. | ◐ (mailto capture live; VITE_CHECKOUT_URL swaps in Stripe without code change) |
| P0 | **License issuance CLI**: vendor keypair management + `sign_license` wrapped as a script; issue/renew/revoke runbook. Verification exists; issuance is manual code today. | | ☑ |
| P1 | **Node-count telemetry for license ceiling**: EE check compares live node count vs `node_ceiling` and warns (never blocks safety — Line 1). | | ☑ |
| P1 | **Design-partner kit**: 2-pager (what they get: fixed price, roadmap influence, case study), 3 slots, success criteria per partner. | | ☑ (docs/design-partner-kit.md) |
| P2 | EU AI Act mapping one-pager (receipt/audit-trail ↔ articles) — cheapest compliance-inbound asset. | | ☑ (docs/eu-ai-act-mapping.md) |

## 6. Ops & quality gates — don't fall over on launch day

| P | Item | Done-when | Status |
|---|---|---|---|
| P0 | **CI fully green on main** including the deploy smoke job. Ecosystem deps now resolve from PyPI (core 0.9.1, eval 0.1.0) — the private-repo token path is obsolete; just verify one real Actions run on main. | ☑ suites / ☐ Actions run | ◐ |
| P1 | **Load smoke**: 50 concurrent SSE subscribers + 100 rps ingest on compose stack; find the first ceiling, write it down honestly in docs. | | ☑ (SQLite **and Postgres 16** numbers in docs/ops-limits.md: ~150 rps ingest ceiling on PG, SSE fan-out is the next bottleneck) |
| P1 | **Error tracking**: Sentry (or logs-only + structured logging) on backend/proxy so launch-day bugs are visible. | | ☑ (AXOR_LOG_JSON structured logs, optional SENTRY_DSN, structured unhandled-error handler) |
| P2 | Backup/restore runbook (pg_dump + trace dir), upgrade runbook. | | ☑ (docs/runbook.md) |

## 7. Sequencing (solo, ~6 weeks)

- **W1–2 — product P0**: MCP onboarding, streaming passthrough, migrations, secure-default compose, SECURITY.md.
- **W3 — release rails**: PyPI + GHCR publish, `uvx` path green on clean machine, retention, concurrent runs.
- **W4 — distribution**: landing + docs site, launch post draft, video, OG unfurl, community files.
- **W5 — money + partners**: Stripe link, license CLI, design-partner kit, node-ceiling warn; benchmark harness runs.
- **W6 — dress rehearsal**: load smoke, error tracking, full E2E on a rented clean VPS from the README alone; fix everything that snags; then launch.

Rule for scope fights: if an item doesn't serve one of the three launch outcomes
— (1) a stranger reaches a caught lie in <10 minutes, (2) a stranger can pay,
(3) a security engineer finds no embarrassing hole in an afternoon — it's P2.
