# axor-identity

The shared **login/identity** service for the Axor platform. One source of
truth for **who a person is** (users), **which organizations they belong to**
(orgs + memberships with a role), and **active sessions**. It authenticates a
human against a credential and issues a short-lived, **EdDSA-signed** access
token plus a revocable refresh token.

It is a *standalone* service: its own FastAPI app, its own database, its own
signing key. The Lab (`axor-lab`) and the control-plane (`axor-backend`) do not
call it on every request — they **verify** its access tokens locally against
the public key published at `/.well-known/jwks.json`. The only shared code is a
small verifier (see `axor_identity.verify`), which those services vendor or
import.

## What it is / isn't

- **Human login only.** Passwords now (argon2); OIDC/SSO is a later add-on,
  gated to the Enterprise tier. Machine-to-machine credentials — the
  control-plane's `ak_…` API keys, the Lab's runtime ingest keys — are *not*
  managed here and keep working as they are.
- **Not an authorization store.** The token carries `org` + `role` + `tier`;
  each service maps those to its own resources (Lab → workspace, CP → tenant
  scope) and enforces its own RBAC.

## Token shape

Access token — a JWT signed with EdDSA (Ed25519), header `kid` selecting the
key from the JWKS:

```
iss  "axor-identity"
sub  user id
eml  email
org  active organization id
role owner | admin | member | viewer   (membership role in `org`)
tier org's plan tier                    (community | team | security | enterprise)
iat, exp                                 (~15 min lifetime)
```

Refresh token — an opaque high-entropy string, stored only as a SHA-256 hash,
bound to (user, org), expiring and revocable. `/v1/refresh` rotates it.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/signup` | create a user + a first org, owner membership |
| POST | `/v1/login` | password → access + refresh (for an org) |
| POST | `/v1/refresh` | rotate refresh → new access + refresh |
| POST | `/v1/logout` | revoke a refresh token |
| GET | `/v1/me` | the caller's identity + memberships (bearer access token) |
| POST | `/v1/orgs/{org_id}/members` | admin adds a member by email + role |
| GET | `/v1/oauth/providers` | which "continue with …" buttons to show |
| GET | `/v1/oauth/{provider}/start?return_to=` | redirect to GitHub / Google (PKCE) |
| GET | `/v1/oauth/{provider}/callback` | back from the provider → `return_to?axor_login=<code>` |
| POST | `/v1/oauth/exchange` | the one-time code → access + refresh (`created`: new account) |
| GET | `/.well-known/jwks.json` | public keys for verifying access tokens |
| GET | `/v1/healthz` | liveness |

## Configuration

- `AXOR_IDENTITY_DATABASE_URL` — async SQLAlchemy URL (default: local SQLite).
- `AXOR_IDENTITY_SIGNING_KEY` — Ed25519 private key, PEM (PKCS#8). If unset a
  key is generated at boot (**dev only** — tokens die with the process and no
  other instance can verify them).
- `AXOR_IDENTITY_KID` — key id advertised in the JWKS and JWT header
  (default derived from the public key).
- `AXOR_IDENTITY_ACCESS_TTL` — access-token lifetime in seconds (default 900).
- `AXOR_IDENTITY_REFRESH_TTL` — refresh-token lifetime in seconds
  (default 1209600 = 14 days).
- `AXOR_IDENTITY_SIGNUP` — `open` | `first-only` | `closed` (default `open`).

### Sign in with GitHub / Google

- `AXOR_IDENTITY_PUBLIC_URL` — where the browser reaches this service, e.g.
  `https://cp.example.com/identity`. Register
  `<public url>/v1/oauth/github/callback` (and `/google/callback`) with the
  provider. Without it no provider is offered.
- `AXOR_IDENTITY_GITHUB_CLIENT_ID` / `_SECRET`, `AXOR_IDENTITY_GOOGLE_CLIENT_ID`
  / `_SECRET` — a provider is offered only when both are set.
- `AXOR_IDENTITY_RETURN_ORIGINS` — other origins a sign-in may return to (the
  Lab's, when it shares this service). Any other `return_to` is refused.

Only an address the provider says is verified is accepted. A provider identity
links by the provider's own user id, so a changed address still lands on the
same account. The first sign-in with an address that already has a password
account links to it; if that account's address was never verified, its
password is dropped and its sessions revoked — whoever set it never proved
they own the address, and the provider just did. Tokens never travel in a URL:
the redirect carries a single-use code (two minutes) that the app trades for a
session.
