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
| `plane.py` | plane service (protocol v0.3): `/command`, `/desired` (SSE), `/telemetry`, `/facts`, `/cascade-stop` |
| `storage.py` | append-only events + runs/desired/reported/facts/pins/keys/share-links/notification-subs; JSON→JSONB on Postgres. Events read back in append order (`events.id`), which is causal order — per-node `seq` is not. Desired-state writes are versioned CAS: a signed command lands only at the version it was signed for. |
| `replay_api.py` | config→`KernelConfig`, scrubber/counterfactual payloads |
| `provenance.py` | value provenance inside ONE run, derived from that run's events on request — no store, because value refs are minted per trace and repeat across runs |
| `attestations.py` | operator attestations: recorded in the fact log here, given their meaning (append-only, revocation-as-an-event, same-keyset revocation) by `axor_sentinel.sentinel.attestation` |
| `coverage.py` | what an attestation discharges: `covers` names fact ids and `level = max(severity(uncovered))`, both imported from `axor_core.kernel.degradation` — the plane holds no second opinion about what an operator's signature bought |
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

Tests: `uv run pytest packages/axor-backend`. The suite runs on SQLite; the
`postgres` marker runs the dialect-sensitive parts (migration chain, JSONB,
batched RETURNING, versioned CAS under real concurrent connections) against a
real Postgres when `AXOR_TEST_POSTGRES_URL` is set, and skips otherwise — CI
sets it. In-process suites use httpx
ASGITransport; `tests/e2e/` (marker `e2e`) boots the real backend + proxy as
subprocesses and drives them over HTTP/SSE — cross-service upload, live audit &
desired streams, webhook delivery, auth enforcement, and process-restart
durability. Run only those with `-m e2e`, or skip them with `-m 'not e2e'`.
