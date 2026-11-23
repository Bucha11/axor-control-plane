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

**Guard mode** (`axor-proxy --guard --manifests …`) makes the proxy a gate:
each call is decided before it reaches the tool — refused by policy (403, or a
JSON-RPC error for MCP), held while the agent is paused or stopped from the
plane (503), refused past the plane's call budget (429). To decide, it reads
the call's arguments and the text of each answer **in memory**; neither is
persisted, and the trace it ships to the plane names arguments with sizes and
hashes, never values. The decision is local: a plane outage removes pause /
stop / budget, not the policy. The proxy itself is in the path — if it is down,
the agent's tools are unreachable — so run it beside the agent.

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

**Single sign-on and SCIM (axor-identity, Business and above).**
- *Who may configure it.* Only a workspace's owner or admin, and only on a
  licence the identity service verified itself with the pinned vendor key. It
  never takes the plane's word for the plan.
- *Which domains.* A workspace claims an e-mail domain only if the verified
  address of one of its owners or admins proves it. Public mail domains are
  refused. One domain belongs to one workspace.
- *OIDC.* Authorization code flow with PKCE, a nonce bound to the verifier,
  and the ID token verified against the provider's JWKS (issuer, audience,
  expiry). `email_verified: false` is refused.
- *SAML.* The XML signature is checked against the certificate the admin
  pinned (signxml), and only the signed element is read afterwards, so a
  wrapped or unsigned assertion is never consulted. XML is parsed without
  DTDs, entities or network access. Issuer, audience, recipient, time window
  (3 minutes' skew) and `InResponseTo` are checked. The request ID is kept
  with the single-use sign-in state, so a replayed response has nothing to
  answer.
- *Enforced SSO* takes passwords and GitHub/Google sign-in away from people
  at the workspace's domains. The owner keeps a password as the way back in
  if the provider breaks.
- *SCIM.* One bearer token per workspace, stored hashed, shown once, and
  replaceable. A deprovisioned person loses their membership and their
  refresh tokens in that workspace, and the plane revokes their API keys
  (service token signed with the identity key, valid 2 minutes). The owner
  cannot be deprovisioned.
- *On the record.* Every change to people and sign-in reaches the plane's
  audit trail. That covers SCIM, an admin's own changes, SSO settings and
  session policy. The login service posts it with its own short-lived token;
  no workspace admin can write those rows.
- *Sessions.* A workspace can cap how long a sign-in lasts, counted from the
  sign-in across refreshes. An admin can end every other sign-in at once. A
  removed member's refresh tokens are revoked at once.
- *SAML keys and options.* Each workspace has its own RSA key pair, made on
  first use and kept in the identity database. It signs AuthnRequests when
  the workspace asks and decrypts assertions encrypted to it: RSA-OAEP only,
  never PKCS#1 v1.5, and every decryption failure gives the same answer. An
  encrypted assertion must still be signed, by the response over the
  ciphertext or inside. IdP-initiated sign-in is off unless a workspace turns
  it on. Then the assertion must answer no request and name this service as
  its audience, and its ID is accepted once. Two provider certificates can be
  held while it rotates.

**EE license.** Verified offline with a pinned vendor Ed25519 key. No
phone-home; expiry degrades EE features to read-only and **never disables
safety features**.

**Known non-goals (current release).**
- The proxy is not a sandbox. By default it observes and injects test-bench
  faults and does not block traffic; in guard mode it refuses tool calls by
  policy, but it sees only the tool boundary — not the model, its context, or
  tools the agent runs in-process (that is the adapter's side).
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
