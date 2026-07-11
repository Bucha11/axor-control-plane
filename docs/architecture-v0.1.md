# Axor Eval — Architecture & Stack (v0.1)

Companion to the UI & Connectivity spec (v0.7). Records technical decisions and their load-bearing rationale. Format mirrors the spec's decisions log: decision → rationale; consequences called out where a choice constrains later work.

---

## 0. The one structural rule

**Replay is the same code as enforcement.** Gates, taint propagation, degradation, Sentinel heating live in `axor-kernel` — a pure Python library: functions over an event sequence, zero I/O, zero framework imports. Both consumers import it:

- the runtime adapter (in-process enforcement, §12.0 of the spec)
- the backend replay engine (§13 scrubber + counterfactuals)

Two implementations of the gate pipeline would eventually diverge and counterfactual replay would silently lie. Every later decision that touches languages or process boundaries is subordinate to this rule.

---

## 1. Trace format — the contract

- **Versioned JSONL**: one event per line; schema defined once in Pydantic models inside `axor-kernel` (single source for runtime, storage, and replay).
- A trace is a **portable artifact**: written locally by the proxy, importable into hosted, feedable to replay, attachable to an EvidenceCase. Storage is secondary to format — "EvidenceCase is the primary artifact" at the byte level.
- Schema version field on every line; replay engine refuses unknown majors (no silent best-effort parsing of governance data).

## 2. Proxy — Python (decision: locked)

- httpx + asyncio; distributed via **uvx/pipx** one-liner + Docker image.
- Rationale: fault injection and scenario logic share code with the eval library and the kernel (rule 0). A Go binary would mean a second implementation of scenario semantics.
- Fallback path if distribution friction materializes: Go proxy driven by declarative JSON scenario configs *generated* by the Python side — interpreter, not reimplementation. Not planned; recorded as the escape hatch.

## 3. Backend — FastAPI + Pydantic + sse-starlette

- Reuses solved SSE infrastructure verbatim: Authorization via fetch-event-source client-side, write-once terminal state, server-side event buffering with Last-Event-ID replay, sticky sessions.
- Control-plane fan-out: desired state persisted in Postgres, LISTEN/NOTIFY → SSE push. No Redis until a single instance stops coping — fewer moving parts.

## 4. Storage

| Concern | Choice | Note |
|---|---|---|
| System of record (events, runs, EvidenceCases, desired state) | **Postgres**, append-only JSONB events + materialized views for UI | ClickHouse only if analytics volume forces it; not v1 |
| Sentinel graph | **Kùzu everywhere** (decision: locked — no PG-CTE interim) | Embedded, Cypher-compatible surface, no server to operate on self-hosted. Hosted: per-tenant embedded DB files → tenant isolation for free. Behind a `GraphStore` interface; existing Neo4j remains a legacy backend on hosted until migrated — interface first, migration unhurried |
| Traces at rest | JSONL files (object storage on hosted, disk on self-hosted); PG stores metadata + pin status | Corpus quota (spec decision #11) enforced on pinned files |

Consequence of Kùzu-everywhere: k-hop default cut (spec decision #6) and branch attestation queries are written once in Cypher-ish and run identically on both deployments — no dual query maintenance.

## 5. Control plane channel — outbound-only from the agent side

- Adapter dials out: SSE subscription for desired state, POST for telemetry. **Zero listening sockets on user infrastructure** — NAT/firewall-friendly, and the command channel opens no inbound surface inside a prod perimeter. Direct consequence of spec §12.0.
- Commands: declarative desired-state, versioned, last-write-wins (spec decision #7). `stopped` absorbing in the state lattice.

## 6. Frontend — React + TypeScript (decision: locked)

- Vite; state: **Zustand** (client/UI state) + **TanStack Query** (server state, SSE-invalidated); `@microsoft/fetch-event-source` is framework-agnostic and carries over unchanged.
- Graphs (taint §8.1, topology §12.1): **Cytoscape.js** (react-cytoscapejs wrapper) — expand-on-click, layouts, compound nodes for subtrees. Sigma.js (WebGL) reserved if Sentinel neighborhoods reach thousands of nodes.
- Scrubber (§13.1): custom component over a virtualized event list (TanStack Virtual); vis-timeline as fallback only.
- Metrics: ECharts.

## 7. Demo — static, no backend

- Recorded traces ship as JSON assets; the replay renderer runs in-browser (TS port of the *rendering*, not of the kernel — verdicts are precomputed into the recorded trace, browser only draws). Landing + demo deploy as a static site: zero infra, best TTFB, and an honest proof that replay is deterministic.

## 8. Repo & deploy

- Monorepo: **uv workspaces** (`kernel` / `proxy` / `backend` / `scenarios`) + pnpm for the frontend.
- Self-hosted: docker-compose (proxy + backend + Postgres; Kùzu is embedded — no extra container) and standalone proxy via uvx.
- Hosted: Fly.io/Railway-class with sticky sessions (process-local SSE buffer requirement). No k8s before real multi-tenant scale.

## 9. Product auth

- Dashboard: OAuth (GitHub first — developer audience).
- Connections (proxy/adapter → backend): scoped API keys.
- Self-hosted: local token. Vault (spec §14.2) is a separate concern on top of an existing secret store, per spec decision #13.

---

## Open (small, non-blocking)

- Kùzu concurrent-writer model on hosted: one writer process per tenant DB — confirm this fits the ingest path before committing the per-tenant-file layout.
- TS types for trace schema: generate from Pydantic (datamodel → JSON Schema → ts) rather than hand-maintain — pick the generator.
- ECharts vs Recharts for the handful of v1 charts — cosmetic, decide at build time.
