# Security

## Reporting a vulnerability

Email **security@axor.dev** (PGP on request). Please include a reproduction;
we aim to acknowledge within 48 hours and to fix or publish a mitigation
within 14 days for high-severity issues. No bug bounty yet — credit given.

Supported: the latest minor release. Older releases receive no patches.

## Threat model (one page)

**What the proxy sees.** The observe-only proxy forwards your agent's tool
traffic. It records **observations only** — tool name, path, method, status,
byte counts, SHA-256 hashes, injected-fault markers. **Raw request/response
bodies are never persisted**, and the `Authorization` header passes through
byte-for-byte — never parsed, never stored, never logged. Exports and share
pages are built from observations, labels and verdicts; body-shaped fields are
scrubbed again at export time (`share._scrub`).

**What the backend stores.** Kernel-schema trace events, plane state
(desired/reported), append-only facts, EvidenceCases, pins, hashed API keys
(SHA-256, secret shown once), share tokens. SQLite for dev, Postgres for real
deployments; schema is migrated by alembic at boot.

**Failure mode: Axor down ⇒ agent unaffected.** The control plane is an
*advisory overlay* (protocol v0.3): the adapter enforces locally with its own
config; the plane channel only carries operator intent. If the backend is
unreachable, a governed agent keeps enforcing its local policy and simply
stops reporting; the observe-only proxy on failure returns 502 for armed runs
and never silently fabricates a response.

**Operator command integrity.** Plane commands are Ed25519-signed over
RFC 8785 (JCS) canonical bytes. The backend verifies as defense in depth, but
the **adapter re-verifies with operator public keys from its own config** — a
compromised backend cannot forge commands. That covers the snapshot the
desired-state stream opens with as well as the deltas after it (protocol v0.3
§3): the snapshot carries the signed command behind each field, the adapter
checks every one, and a field no command accounts for refuses the whole
snapshot. Until v0.3 the snapshot was unsigned, and reconnect — which is
routine, not exceptional — was a way around this paragraph. Floats are rejected
in signed payloads by construction. Dev mode (`AXOR_ALLOW_UNSIGNED=1`) disables
this and is loudly logged at boot.

**API access control.** Opt-in bearer auth: unset token = open (dev), set
token = enforced. Scoped API keys (`read < ingest < operate < admin`) are
least-privilege and stored hashed. Share links are scoped by design — one
case, its own unguessable token (128-bit), revocable, no navigation to
anything else.

**EE license.** Verified offline with a pinned vendor Ed25519 key. No
phone-home; expiry degrades EE features to read-only and **never disables
safety features**.

**Known non-goals (current release).**
- The proxy is not a sandbox: it observes and injects test-bench faults; it
  does not block traffic (enforcement is the adapter's job).
- Single-instance backend; no HA story yet.
- stdio MCP servers are proxied via a local gateway: the proxy *spawns the
  server process from operator-supplied config*. Registration is accepted
  from loopback callers only (`AXOR_ALLOW_REMOTE_STDIO=1` overrides — don't,
  unless the proxy port is already access-controlled), so an exposed proxy
  port is not a remote-exec endpoint. Treat the command like any other
  deploy-time config; it is never taken from runtime/agent input.

## Hardening checklist for a real deployment

1. Set `AXOR_API_TOKEN` (e.g. `openssl rand -hex 24`) and mint scoped keys.
2. Set `AXOR_OPERATOR_KEYS` and `AXOR_ALLOW_UNSIGNED=0`.
3. Put the stack behind TLS (nginx/ingress); the compose file serves plain HTTP.
4. Use Postgres, not SQLite; back up `pgdata` and the proxy trace volume.
5. Self-host the proxy next to your tools; a hosted proxy sees tool traffic.
