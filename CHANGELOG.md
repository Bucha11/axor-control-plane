# Changelog

The Control Plane ships as three packages from one tag (`axor-backend`,
`axor-proxy`, `axor-identity` — all carrying the tag's version) and as two
images (`axor-platform`, `axor-frontend`). Self-hosted operators upgrade by
tag; `deploy/offline` carries the same release to a machine with no network.

## 0.7.4 — 2026-11-28

### Added

- **Change the plan in place.** On a plan paid by card, Pricing offers
  "Switch to …": the workspace's one subscription moves to the other rung
  (`POST /v1/license/plan`, through the vendor's billing service) instead of a
  second checkout. Needs axor-billing 0.1.1.
- **Revocations take effect within seconds.** Identity publishes a revocation
  feed (ended sessions, membership epochs) the plane checks: removing a
  member, changing a role or logging out no longer waits for the access token
  to expire. Reusing a rotated refresh token ends its whole session. Identity
  migration `0012_revocations`.
- **Optimistic concurrency** on the coding policy and the tool policy: a stale
  tab gets a 409 naming what changed, never "saved".

### Changed

- **Billing reads as a bill:** one month-by-month table on every plan (Free
  included), each month opening to its statement; on Free, one line for the
  paid plan that fits.
- **Roles:** a 403 is never resent after a refresh; viewers are not offered
  write controls; the license, vault setup and plan are the owner's; a guest
  is not an admin; on Free the brake holds "your agents".
- **SQLite** runs with write-ahead logging and a busy timeout: a read meeting a
  write no longer fails with "database is locked".
- The governed demo node stays 10 minutes and says when it left.

### Fixed

From a third, journaled QA pass over the hosted plane:

- An upgrade bought mid-period reaches identity (SSO, SCIM and the session
  limit unlock on Business/Team); boot hands held licenses to identity again.
- Every chaos mode finishes; Safe to ship? no longer passes a run that used a
  refused tool; a revoked operator key is named as revoked.
- Sign-up validates lengths and creates user and workspace together; the
  e-mailed invite link opens the invite page; sign-in errors in the URL are
  fixed words; a guest restart backs off.
- A failed read says so instead of offering defaults; the tour follows the
  workspace's journey; "make it a rule" on the example writes nothing; trusted
  sources refuse public-suffix wildcards and user-content hosts.
- The audit trail records who acted in which role, and upgrades.

## 0.7.3 — 2026-11-27

### Fixed

From a second exploratory QA pass over the hosted plane:

- **A malformed link no longer blanks the app**; a page that fails to render
  says so beside a working app.
- **The tour's example seeding no longer fails at random**: two reseeds of one
  demo run take turns.
- **"Spawn a governed demo node" appears in Control**: its heartbeats carry the
  credential its run was uploaded with.
- **The product's example team memory is read-only.**
- **Signing:** facts and cascade stop are signed by the browser's own operator
  key, like every command; commands record the operator who signs them; one
  name, "operator keys".
- **Roles:** viewers, members and admins are not offered actions their role
  cannot take (keys, alerts, the vault's owner-only forms).
- **Settings and sign-in:** webhook refusals no longer reveal internal
  addresses; webhook URLs are limited (500); a session that cannot refresh says
  "sign in again"; identity logs why an SMTP server refused a message; a guest
  is not asked for what a guest cannot have.
- **Double-clicks** send once on every button that creates something; the tour
  ends on Escape; the in-app dialog keeps focus inside it.
- **Operating screens:** real timestamps on governed demo events, one local time
  format, the activity feed in order, cap values validated, Replay search
  limits, `#/catch?auto=1` opened directly works, Trusted sources refuses paste
  sites and Q&A forums, a just-shared link is no longer briefly called revoked,
  and layout fixes at 390px.

## 0.7.2 — 2026-11-27

### Added

- **A workspace makes its own vault tokens.** Without a deployment
  `AXOR_VAULT_*_TOKEN`, a workspace admin mints its own token for dispensing
  tool credentials and for requesting signatures (Settings → security; shown
  once, only its hash kept, replacing it cuts off the old one). From then on
  that subsystem asks for it in the `X-Vault-*-Token` header axor-proxy already
  sends. A deployment token still rules every workspace. New:
  `/v1/vault/tokens…`.

### Fixed

- **No native dialogs.** Every `confirm()` / `prompt()` (stop, cap, replan,
  inject, holds, revokes…) is an in-app dialog: Enter confirms, Escape cancels.
- **Days are the viewer's local day** where they come from a full timestamp
  (invites, legal holds, SCIM and machine keys); calendar dates stay as given.
- **18 layout utilities used across the UI were never defined** (`mb-4`,
  `px-3`, `flex-1`, …): stacked cards no longer touch. A test now fails on any
  layout utility the stylesheet does not define.

## 0.7.1 — 2026-11-27

### Changed

- **A hosted plane takes only sealed tool credentials.** With `AXOR_HOSTED`,
  vault enrol and rotate refuse a plaintext `secret`: a credential comes sealed
  to the workspace's own key (`axor-proxy vault keygen`, then `sealed_secret`).
- **The security posture fits a plane with many tenants.** On a hosted plane it
  reports command signing by each workspace's own keys, sealed credentials,
  and vault tokens per workspace as not offered yet — instead of asking for a
  deployment operator key, deployment vault tokens and `AXOR_ORG`.

## 0.7.0 — 2026-11-27

### Added

- **Run history by plan on the hosted plane.** A workspace's runs are kept for
  its plan's window: Free 7 days, Coding and Business 30, Team 90, Security a
  year, Enterprise as long as it chooses. The window follows the license held,
  even an expired one, so a renewal that slips by a day does not cut history
  short; `AXOR_RETENTION_DAYS`, when set, is a ceiling. Self-hosted planes keep
  their own `AXOR_RETENTION_DAYS` as before. Pricing and Settings → billing say
  what is kept; `/v1/license/entitlements` carries `retention_days`.

### Changed

- **An open legal hold stops run pruning too**, not only the audit trail's.
- **Trusted sources refuse a newly added code host** (github.com, gitlab.com,
  bitbucket.org, codeberg.org, githubusercontent.com and their subdomains). One
  a team already trusted stays until it is removed.
- **One install command for axor-wrap across the app**; the worker's is
  `uv tool install --upgrade 'axor-wrap[plane,eval]'`.
- **Times are shown in the viewer's local time** everywhere.
- The prod compose file passes `AXOR_SMTP_URL` / `AXOR_MAIL_FROM` to the plane.

### Fixed

From an exploratory QA pass over the hosted plane:

- A demo node or tree spawned from the page lands in the spawner's workspace
  (`X-Axor-Upload-Token`), and the in-app experiment finishes on a proxy that
  guards its control surface; a refused upload or claim is reported in words.
- A refused request shows the plane's own reason, never a raw body — every
  screen. A 403 is retried after a token refresh only for a scope refusal, so
  failed POSTs are no longer sent twice.
- A new tab signs in instead of showing "signed in" while every request fails.
- The product's example skill can never be turned on for the real team.
- A guest refused for too many visitors (429 now carries `Retry-After`) is told
  so and let in when allowed; requests wait for the session instead of firing
  a burst of 401s on load.
- Back / forward move Connect and Pricing; Replay keeps `?cursor=` in step.
- Prices agree between Pricing, Connect and Enterprise; the calculator only
  climbs the ladder and sends very large fleets to Enterprise; the $0.00 card
  checkout explains itself.
- Form validation: numeric fields hold what is typed and refuse what is
  invalid; long values carry their limits and wrap; double-clicks make one key,
  invite or subscription.
- Layout: the topology graph is as tall as its rows, graph labels are not
  clipped, the signed-in header fits a phone, Settings → security cards stay in
  their column.
- Smaller fixes to Settings, audit, alerts, Safe to ship?, Control, the example
  fleet and Incidents.

## 0.6.2 — 2026-11-26

### Added

- **Run a skill eval from the UI, on your own machine.** Skills → Does a skill
  help? queues the eval on your `axor-wrap worker` (axor-wrap 0.4.1:
  `pip install 'axor-wrap[eval]'`, then `axor-wrap worker` in the repository)
  instead of handing out a script to paste. The page shows how far along it is,
  cancels it between runs, and opens the report. A job runs only on its
  owner's machine — it starts a coding agent and a shell command there — and a
  machine that goes quiet loses its job, which is never re-run on its own.
  New: `machines`, `machine_jobs` (migration 0040), `/v1/machines…`,
  `/v1/machine-jobs…`.

### Fixed

- **The account menu** says who is signed in and which workspace, and points
  to Settings for the team, who may, sign-on and keys — the whole of them in a
  300px popover ran off the bottom of the screen.
- **"planned" chips** in Pricing no longer stretch into ovals beside a
  two-line feature (`items-baseline` was used but never defined), and
  Control's spawn buttons no longer touch.

## 0.6.1 — 2026-11-26

Fixes from the first card payment on prod. Nothing touches enforcement.

### Fixed

- **A hosted guest is told to sign in to pay by card.** The plane refuses a
  guest `/v1/license`, so Pricing had no organization to name and the paid
  button stayed a mailto that, with no mail client, did nothing.
- **The frontend's healthcheck** asks `127.0.0.1`, not `localhost`: in the
  image `localhost` resolves to `::1` first and nginx listens on IPv4, so a
  serving frontend read "unhealthy". Root, prod and self-hosted compose.
- **`backup.sh`** writes the axor-billing copy owner-only, like every dump
  (`docker cp` keeps the container's mode and ignores `umask`).

### Changed

- **Requires axor-eval 0.3.3**: a made-up result in live work — a tool that
  really failed, a call the operator held — is a case too, as Incidents and
  the live-work receipt show.
- CI runs its tests again: `ruff` had stopped the job before them, and a
  stale-monitor test raced the app's own background sweep.

## 0.6.0 — 2026-11-26

A workspace can pay by card: the ladder is sold through Paddle, and the plane
does its half of the vendor licensing service (axor-billing) — reports the
usage it is billed for, renews and hands identity the plan it pays for. Agents
of a pair can write to each other. Nothing here touches enforcement.

### Added — card payments

- **Checkout in Pricing.** A paid plan opens Paddle checkout naming who the
  license is for (`licensed_to`: the tenant on hosted, `AXOR_ORG` on an
  install) and whether this is the vendor's hosted plane (`AXOR_HOSTED`, now
  in `/v1/license/status`). `VITE_PADDLE_CLIENT_TOKEN`, `VITE_PADDLE_ENV` and
  `VITE_PADDLE_PRICES` are build arguments the release passes from repository
  variables; unset keeps the mailto / `VITE_CHECKOUT_URL` link.
- **Usage report carries developers** (`included_developers`,
  `active_developers`, `billable_developers`, as the invoice counts them):
  Coding and Business are billed per developer.
- **Identity follows a renewal.** A license the plane installs on its own — a
  renewal, or an operator's for a tenant — is handed to identity's new
  `POST /v1/orgs/{id}/license/renewal` (no caller; vendor-signed, this
  workspace, in date, later than the plan in force), so single sign-on and SCIM
  no longer lapse at the old date. `AXOR_IDENTITY_URL`, defaulting to
  `AXOR_IDENTITY_JWKS_URL`'s origin.
- **deploy/prod**: `AXOR_HOSTED`, `AXOR_LICENSE_RENEWAL_URL`,
  `AXOR_USAGE_REPORTING` for the backend; optional Caddy `sites/*.caddy`;
  `backup.sh` saves axor-billing's database when it runs on the host.

### Security — card payments

- **The renewal request carries the license in force** (`license_json`), not
  only its name: a vendor answering a bare organization name would hand any
  stranger that organization's paid license and bill it on their word.

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
- **The pair feed is live, both ways.** `GET /v1/pair/{token}/stream` (SSE,
  resumes from `Last-Event-ID`) pushes each step as the sidecar reports it,
  the notes whenever one is sent, given or decided, and the hold — the page
  no longer polls every two seconds. The feed shows the developer's requests,
  what the agent says on the way, and its edits as diffs (Edit, MultiEdit,
  Write, Codex's apply_patch), beside the calls, checks and answer it showed.

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
