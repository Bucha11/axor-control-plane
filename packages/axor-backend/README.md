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
| `plane.py` | plane service (protocol v0.3): `/command`, `/desired` (SSE), `/telemetry`, `/consumed`, `/facts`, `/cascade-stop`, `/coverage`, `/probe-report`, `/repair` (+ `/repair/excision-request`), `/reputation`, plus the fleet views `/nodes` and `/topology` |
| `storage.py` | append-only events + runs/desired/reported/facts/pins/keys/share-links/notification-subs; JSON→JSONB on Postgres. Events read back in append order (`events.id`), which is causal order — per-node `seq` is not. Desired-state writes are versioned CAS: a signed command lands only at the version it was signed for. |
| `replay_api.py` | config→`KernelConfig`, scrubber/counterfactual payloads |
| `provenance.py` | value provenance inside ONE run, derived from that run's events on request — no store, because value refs are minted per trace and repeat across runs |
| `attestations.py` | operator attestations: recorded in the fact log here, given their meaning (append-only, revocation-as-an-event, same-keyset revocation) by `axor_sentinel.sentinel.attestation` |
| `coverage.py` | what an attestation discharges: `covers` names fact ids and `level = max(severity(uncovered))`, both imported from `axor_core.kernel.degradation` — the plane holds no second opinion about what an operator's signature bought |
| `signing.py` | operator command signing — delegates JCS to `axor_core.kernel.canonicalize` |
| `notifications.py` / `monitor.py` | webhook triggers (retries + dead-letter); node-stale sweep. Seven triggers: `level_transition_up`, `heat_threshold`, `evidence_run`, `node_stale`, `regression_failed`, `behavioral_drift`, `license_expiring` |
| `tenancy.py` | the current request's organization, as a ContextVar the Store reads when it builds each query. `PUBLIC_ORG` is the single implicit tenant for everything that is not an identity login, so a single-tenant deployment behaves exactly as before |
| `identity_client.py` | verifies `axor-identity` access tokens against its JWKS (fetched once at boot, or pinned inline), and maps `org` + `role` onto scopes + tenant |
| `vault_creds.py` / `vault_dispense.py` / `vault_signing.py` | the two vaults behind their own credentials: tool-credential enrolment/rotation/revocation and sink-side dispense (node-bound, optionally node-signed, optionally sealed to a key the backend does not hold), and the signing vault with its own operator allowlist. Both keep an audit log |
| `lab_import.py` / `lab_export.py` / `lab_trace.py` | the `axor-lab` seam: deploy packages in, evidence and pins out, and a Lab trace read as a kernel trace. Gates and policy come from `axor_core.policy`, never from a local copy |
| `drift_evidence.py` | the BEHAVIORAL_DRIFT projection — `axor-probe`'s battery re-keyed into `axor-eval`'s grader. Derived on read and deliberately never stored: ui-spec §8.2 keeps the health check out of every Eval score |
| `evidence.py` / `offload.py` / `broadcast.py` | EvidenceCase derivation from an uploaded run; large-payload offload; the in-process SSE fan-out behind `/desired` and the audit stream |
| `limits.py` / `env.py` / `errors.py` / `clock.py` / `observability.py` | per-request ceilings (`AXOR_MAX_*`), the one reader for every env var, the typed error shapes, the injectable clock, and JSON logging + optional Sentry |
| `wrap_api.py` / `demo.py` | `axor-wrap` code analysis (`/v1/wrap/scan`, `/v1/wrap/manifests`); demo seeds for the in-app shortcuts |
| `share.py` | EvidenceCase HTML + dependency-free PDF receipt, revocable links |
| `auth.py` | opt-in master token + scoped API keys |
| `ee/` | Enterprise Edition (offline Ed25519 license) — commercial licence, see `ee/LICENSE` |

Env: `AXOR_DATABASE_URL`, `AXOR_API_TOKEN`, `AXOR_OPERATOR_KEYS` (JSON op→hex),
`AXOR_ALLOW_UNSIGNED`, `AXOR_VENDOR_PUBKEY`, `AXOR_RETENTION_DAYS`,
`AXOR_IDENTITY_JWKS_URL` / `AXOR_IDENTITY_JWKS` / `AXOR_IDENTITY_ISSUER`, the
two `AXOR_VAULT_*_TOKEN` credentials, `AXOR_STALE_AFTER` and the `AXOR_MAX_*`
ceilings. `.env.example` documents each one, and `tests/test_env_surface.py`
fails if this backend reads a variable that file does not list, or that file
lists one nothing reads.

There is one *reader*, `env.py` — an unparseable value is a refusal naming the
variable, never a quietly assumed default. The request-time settings are
resolved once into the frozen `AppConfig` in `config.py`, and its dataclass
fields are that list; the two that are not request-time live where they are
used — `AXOR_STALE_AFTER` in `monitor.py` (the sweep owns its own cadence) and
the `AXOR_MAX_*` ceilings in `limits.py`, read at import so a typo is a backend
that refuses to start rather than a ceiling silently back at its default.

`AXOR_VENDOR_PUBKEY` is the licensing trust root: `/v1/license/verify`
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
