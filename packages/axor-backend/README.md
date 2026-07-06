# axor-backend

FastAPI system-of-record + plane service. Persists and fans out; it never
interprets governance — replay/degradation is `axor_core.kernel`'s.

Run: `AXOR_ALLOW_UNSIGNED=1 uv run uvicorn axor_backend.main:app --factory --port 8400`
(SQLite by default; set `AXOR_DATABASE_URL=postgresql+asyncpg://…` for Postgres/JSONB).

Modules:

| Module | Responsibility |
|---|---|
| `app.py` | app factory: routes, auth middleware, lifespan (stale monitor) |
| `plane.py` | plane service (protocol v0.2): `/command`, `/desired` (SSE), `/telemetry`, `/facts`, `/cascade-stop` |
| `storage.py` | append-only events + runs/desired/reported/facts/pins/keys; JSON→JSONB on Postgres |
| `replay_api.py` | config→`KernelConfig`, scrubber/counterfactual payloads |
| `graph.py` | `GraphStore` protocol; `InMemoryGraphStore` (default) + `KuzuGraphStore`; trace→derivation folding |
| `signing.py` | operator command signing — delegates JCS to `axor_core.kernel.canonicalize` |
| `notifications.py` / `monitor.py` | webhook triggers (retries + dead-letter); node-stale sweep |
| `share.py` | EvidenceCase HTML + dependency-free PDF receipt, revocable links |
| `auth.py` | opt-in master token + scoped API keys |
| `ee/` | Enterprise Edition (offline Ed25519 license) — commercial licence, see `ee/LICENSE` |

Env: `AXOR_DATABASE_URL`, `AXOR_API_TOKEN`, `AXOR_OPERATOR_KEYS` (JSON op→hex),
`AXOR_ALLOW_UNSIGNED`, `AXOR_VENDOR_PUBKEY`, `AXOR_STALE_AFTER`.

Tests: `uv run pytest packages/axor-backend`.
