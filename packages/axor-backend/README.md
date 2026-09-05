# axor-backend

FastAPI system-of-record + plane service. Persists and fans out; it never
interprets governance — replay/degradation is `axor_core.kernel`'s.

Run: `AXOR_ALLOW_UNSIGNED=1 uv run uvicorn axor_backend.main:app --factory --port 8400`
(SQLite by default; set `AXOR_DATABASE_URL=postgresql+asyncpg://…` for Postgres/JSONB).

Modules:

| Module | Responsibility |
|---|---|
| `app.py` | application factory — resolves config, builds state, installs the auth gate, registers routers. Nothing else. |
| `config.py` | every `AXOR_*` variable, resolved once into one frozen `AppConfig` |
| `deps.py` | request-scoped access to the long-lived collaborators (`StoreDep`, `GraphDep`, …) |
| `security.py` | principal resolution + the auth middleware; the *policy* it applies lives in `auth.py` |
| `lifecycle.py` | lifespan: boot warnings, rehydrating the in-memory projections, retention and EE-scheduler sweeps |
| `licensing.py` | verified EE licenses per organization + the paid-feature gate |
| `corpus.py` / `traces.py` | the pinned-corpus report (shared by route and scheduler); reading a run back as a kernel trace |
| `routers/` | the HTTP surface, one module per domain; `ALL_ROUTERS` is the single registration point |
| `plane.py` | plane service (protocol v0.2): `/command`, `/desired` (SSE), `/telemetry`, `/facts`, `/cascade-stop` |
| `storage.py` | append-only events + runs/desired/reported/facts/pins/keys/share-links/notification-subs; JSON→JSONB on Postgres. Events read back in append order (`events.id`), which is causal order — per-node `seq` is not. Desired-state writes are versioned CAS: a signed command lands only at the version it was signed for. |
| `replay_api.py` | config→`KernelConfig`, scrubber/counterfactual payloads |
| `graph.py` | taint graph — a derived index over the event log; `InMemoryGraphStore` (default, rebuilt from the DB at boot) + `KuzuGraphStore`; trace→derivation folding |
| `signing.py` | operator command signing — delegates JCS to `axor_core.kernel.canonicalize` |
| `notifications.py` / `monitor.py` | webhook triggers (retries + dead-letter); node-stale sweep |
| `share.py` | EvidenceCase HTML + dependency-free PDF receipt, revocable links |
| `auth.py` | opt-in master token + scoped API keys |
| `ee/` | Enterprise Edition (offline Ed25519 license) — commercial licence, see `ee/LICENSE` |

Env: `AXOR_DATABASE_URL`, `AXOR_API_TOKEN`, `AXOR_OPERATOR_KEYS` (JSON op→hex),
`AXOR_ALLOW_UNSIGNED`, `AXOR_VENDOR_PUBKEY`, `AXOR_STALE_AFTER`. Every one of
them is resolved in `config.py` and nowhere else — the dataclass fields are the
list. `AXOR_VENDOR_PUBKEY` is the licensing trust root: `/v1/license/verify`
checks against it and refuses a vendor key supplied in the request.

Tests: `uv run pytest packages/axor-backend`. In-process suites use httpx
ASGITransport; `tests/e2e/` (marker `e2e`) boots the real backend + proxy as
subprocesses and drives them over HTTP/SSE — cross-service upload, live audit &
desired streams, webhook delivery, auth enforcement, and process-restart
durability. Run only those with `-m e2e`, or skip them with `-m 'not e2e'`.
