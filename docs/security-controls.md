# Security controls narrative (SOC2-lite — no certification claimed)

For reviewers who need control-by-control answers before a cert exists.
Honest scope: solo-maintained OSS; controls below are technical and
verifiable in code/CI, not audited attestations. DPA: template pending
counsel — design partners get a mutual NDA + this page meanwhile.

**Access control** — opt-in bearer auth; scoped keys (read, ingest, operate,
admin — an exact set, NOT a ladder that implies: an `admin` key does not read),
stored SHA-256, shown once. Master token = env, never persisted. A key delegates
only scopes it holds, and a node-bound key mints only for its own node, so
minting is not a way around either wall. Every mint and revoke is appended to a
custody log that outlives the key (`GET /v1/keys/audit`).
**Change management** — every change via PR CI: lint, 100+ tests incl. E2E
booting the real stack, dependency audit (pip-audit + pnpm audit), deploy smoke.
**Data handling** — raw tool bodies never persisted (observations: sizes/hashes);
exports scrubbed again at render; share links scoped + revocable; retention
window configurable (AXOR_RETENTION_DAYS). Backups: see docs/runbook.md.
**Integrity** — operator commands Ed25519-signed over RFC 8785 canonical bytes;
adapter re-verifies with its own keys, deltas and reconnect snapshots alike
(backend compromise ≠ command forgery);
facts append-only; event log append-only with idempotent ingest.
**Availability** — advisory overlay: platform outage does not affect governed
agents. Single-instance backend (documented limit); healthchecks in compose.
**Vulnerability management** — SECURITY.md disclosure (48h ack target);
dependency audit gates CI; SBOM artifact published per run.
**Logging/monitoring** — structured JSON logs (AXOR_LOG_JSON), optional Sentry,
webhook alerting with dead-letter honesty, node-stale detection.
