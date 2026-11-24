# Changelog

The Control Plane ships as three packages from one tag (`axor-backend`,
`axor-proxy`, `axor-identity` — all carrying the tag's version) and as two
images (`axor-platform`, `axor-frontend`). Self-hosted operators upgrade by
tag; `deploy/offline` carries the same release to a machine with no network.

## Unreleased

### Added — agents of a pair, to each other

- **Agent-to-agent messages.** A coding session's agent writes to a peer
  session's agent through the sidecar (`axor-wrap`'s `axor_pair`): another
  session of the same developer, or one joined by an open pair either way
  round. Provider-blind — Claude Code to Codex and back.
  `GET /v1/coding/peers/{node}`, `POST /v1/coding/notes/{node}/send`,
  `GET /v1/coding/notes/{node}/with/{peer}`.
- **Manual or auto, per developer.** Between one developer's own sessions a
  message is given at once. From a teammate's agent it waits for the
  receiving developer's OK (`manual`, the default) — they are told in their
  agent and send it on or dismiss it on the session panel — or goes straight
  through (`auto`). `POST /v1/coding/say/{node}/held/{id}`,
  `GET|PUT /v1/coding/say/{node}/mode`.
- **The sender's taint rides with the text** (`pair_notes.taint`), shown on
  the session panel and in the pair feed; every message, OK, dismissal and
  mode change is on the audit trail.
- Migration `0034`: `pair_notes.from_node`, `taint`, `held`, `dismissed_ts`,
  `told`.

### Security

- **What widens for a coding agent must be signed.** A sidecar connected with
  operator keys (`axor-wrap connect --operator-key`) takes the team's rules,
  observe mode, exceptions, trusted sources and team skills only from a policy
  an operator signed. The plane serves the document to sign
  (`GET /v1/coding/policy/document`), keeps the signature
  (`PUT /v1/coding/policy/signature` — refused when stale, not newer, or not
  verifying against the plane's keyring) and serves it with the policy; team
  skills are named by the hash of their files. The policy screen says whether
  what stands is signed, and signs it when the browser holds a signing key.
- **Operators sign from their own browser.** Settings → "make a signing key
  in this browser": an Ed25519 pair made by WebCrypto, private half
  non-extractable in IndexedDB — the plane keeps only the public half, bound
  to the account (`operator_keys`, migration `0038`; `GET|POST /v1/operators`,
  `DELETE /v1/operators/{id}`). Commands, the coding policy and notes are
  signed with it; the plane checks them against it as against its keyring,
  and records the signature for the node. A compromised plane cannot sign.
- **A vouch instead of a reconnect.** An operator vouches for another's key
  (`PUT /v1/operators/{id}/endorsement`, signed with their own); machines add
  a key only when one they already trust vouched for it, transitively.
  Revocation is served and taken unsigned — it only narrows — and a machine
  whose every key is revoked trusts nobody, never the plane.
- **Connect in the signed posture by default.** The Connect page, "My keys"
  and the laptop key preset put the plane's operator keys into `axor-wrap
  connect` (`GET /v1/coding/operator-keys`) and show their fingerprint; a
  plane with none says it signs nothing yet. An already-connected machine
  runs the same command again, without `--key`.
- **Signed notes.** "Say to your agent" and pair notes carry an operator's
  signature when the browser can sign (`pair_notes.signed`, migration `0036`);
  a sidecar with operator keys gives an unsigned one as outside text.
- **Whose machine is whose** (`developer_identities`, migration `0035`): a
  machine's developer id is bound to the person whose own key first reports
  it; a second claim disputes it and it names nobody until an owner or admin
  settles it (Settings, `GET|PUT /v1/identities`). Sessions are read through
  the bindings, never rewritten.
- **A tamper-evident audit trail.** Every custody-log row carries the hash of
  the row before it (`vault_audit.prev_hash`, `row_hash`, migration `0037`);
  `GET /v1/audit/verify` and the audit screen show whether it is untouched, or
  the first row where an edit, a removal or an inserted row breaks it.
  Retention pruning the oldest end keeps it whole. The compliance pack carries
  the chains' heads, and is signed by default when the browser holds a signing
  key.

### Fixed

- The config builder said a catastrophic call is "refused unless a person
  approves it" — no approval exists on the plane. It now says what is true:
  refused, unless the agent's own approval hook (axor-core escalation) grants
  that one call.

## 0.5.0 — 2026-11-23

The paid ladder becomes real: a workspace can be signed into by a company's
identity provider, say who may do what, and hand a reviewer one document for a
period. Nothing here touches enforcement — every gate, taint decision and
fail-closed default is where it was.

### Added — the Business plan

- **Single sign-on per workspace**, over OpenID Connect or SAML 2.0. A
  workspace claims only e-mail domains proved by an owner's or admin's own
  verified address; public mailbox domains are refused. Enforcing it takes
  passwords and GitHub/Google away from those domains while the owner keeps a
  password, so a misconfigured provider never locks the workspace out. SAML
  carries signed AuthnRequests, assertions encrypted to the workspace's own
  key (RSA-OAEP only), sign-in started from the provider's dashboard, and two
  certificates while one rotates.
- **SCIM 2.0 provisioning.** The provider creates people, deactivates them,
  renames them and changes their address; its groups map to roles. A person it
  deactivates loses their membership, their sessions in that workspace, and the
  keys their machines report with.
- **Who may** — stop a teammate's session, pull the team's brake, grant
  exceptions, change skills or memory, operate production: granted to members
  as a whole or to named people.
- **The whole audit trail**, with CSV and JSON-lines export, the workspace's
  own retention, and a monthly report of who acted on whose agent. Acts on
  people and sign-in reach it from the login service, which posts them with a
  short-lived token only it can sign.
- **Session limits** — how long a sign-in lasts, counted from the sign-in
  across refreshes, and signing everyone else out at once (that one on every
  plan).

### Added — the Enterprise plan

- **The compliance pack**: any period of the whole governed surface as one
  document — what was governed, every intervention with who and why, the
  attestations, policy and access changes, credentials — each section naming
  its source, with a table of what each answers and a sha256 over the body.
  Readable as a self-contained page or saved as JSON, and signable through the
  vault's own signing custody.
- **Legal hold**: while one is on, nothing on the audit trail is pruned,
  whatever retention says. Placing and lifting one are on the trail.

### Added — coding agents

- **Agent trees by stage**, with a Claude Code subagent shown under its session
  and an activity feed of one line per call.
- **Session controls**: pause or stop a tree as a whole, say something to your
  own agent, pull the team's brake. Held calls are counted as control, not as
  refusals.
- **Pair sessions by invitation** — a teammate does not operate someone else's
  session uninvited.
- **Developer seats**: a limit an admin sets, this month's bill as it stands,
  and what each plan would cost from the workspace's own usage.
- **Policy exceptions and trusted sources**, and incident triage.

### Added — deployment

- **`deploy/offline`**: one archive — images, stack, installer, a manifest of
  every image digest and checksums — that installs on a machine with no route
  out. TLS there is your own certificate or Caddy's local CA; ACME is off. A
  compose override pins every image to what was loaded.
- The login service's settings reach the self-hosted and production compose
  files: its public address, the vendor key, the plane's address for SCIM key
  revocation, GitHub/Google and mail.

### Changed

- **No paid rung includes developer seats.** Seats are one product and
  governed nodes another: a rung says how many production agents it covers and
  at what price, and a developer who runs coding agents is a Business seat on
  top. Team used to include five, which made it Business plus $99 and strictly
  better.
- A member steers agents with their own key; a removed teammate loses theirs.
- Onboarding connects a machine with one command instead of shell exports.
- Examples step aside for good once the workspace's own agents report.

### Fixed

- A plain `docker compose up` no longer stops on Axor Lab's control token:
  compose reads the variables of services behind an inactive profile too, so
  both installers now always generate it.
- A session's title and profile, sent at once, both land.
- A pair opened on someone else's session before invitations let nobody in.
- Only what a guest did moves into the account that claims it.

### Requires

- axor-core 0.13.1, axor-eval 0.3.1, axor-wrap 0.4.0 (the pin moves to
  `>=0.4,<0.5`), axor-sentinel 0.4.3, axor-probe 0.2.1.

## 0.4.0 and earlier

Before this file. See the git history and the GitHub releases.
