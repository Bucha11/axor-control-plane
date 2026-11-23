# Self-hosted Axor Control Plane

One company, one host, one command.

```sh
curl -fsSL https://get.docker.com | sh
git clone https://github.com/Bucha11/axor-control-plane /opt/axor
cd /opt/axor/deploy/self-hosted
./bootstrap.sh axor.example.com you@example.com              # Control Plane
./bootstrap.sh axor.example.com you@example.com --with-lab   # … and Axor Lab
```

That is the whole install. The script generates every secret into `.env`
(`chmod 600`, never overwritten on a re-run), pulls the images, starts the
stack and prints your operator token.

## With Axor Lab

`--with-lab` adds Lab on `lab.<your hostname>` (or pass the hostname after the
flag). It needs its own DNS `A` record to the same server. Lab signs people in
through this install's identity service, so **the same account works in both**:
register once on the Control Plane, log in to Lab with it. Lab offers no
anonymous guests here — a company install is private.

Adding Lab later is the same command on the existing install: it fills in only
Lab's settings, creates its database, and leaves every other secret alone.

## Before you run it

Two things, both outside this repository:

- **DNS.** An `A` record for your hostname pointing at the server, *not* behind
  a CDN proxy. Let's Encrypt validates over HTTP on port 80, and a proxy that
  cuts idle connections also breaks the live run stream (SSE, up to an hour per
  connection).
- **Ports.** 22, 80 and 443 open in the provider's firewall. Do not rely on
  `ufw` alone: Docker writes its own iptables rules and bypasses it. This
  compose publishes only Caddy's ports.

Sizing: 2 vCPU / 4 GB is enough. Idle, the stack uses about 300 MB; reading a
200k-event run back peaks around 400–550 MB (`docs/ops-limits.md` has the
measured numbers). x86_64 only — the published images are amd64.

## The two settings

Everything else in `.env` is a generated secret. These are the choices:

| | |
|---|---|
| `CP_HOST` | the hostname this install answers on |
| `AXOR_IDENTITY_SIGNUP` | `first-only` (default), `closed`, or `open` |

`first-only` means the first account created on the install claims it and
becomes the owner; everyone after that is refused. That is what makes it a
single-tenant install rather than a public sign-up page on your server.

Closing signup does **not** close the install. Login, refresh and membership
keep working, so you add colleagues by e-mail:

```sh
TOK=$(curl -sS -X POST https://$CP_HOST/identity/v1/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","password":"…"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
ORG=$(curl -sS https://$CP_HOST/identity/v1/me -H "Authorization: Bearer $TOK" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["active_org"])')

curl -sS -X POST "https://$CP_HOST/identity/v1/orgs/$ORG/members" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"email":"colleague@example.com","role":"member"}'
```

Roles are `viewer` < `member` < `admin` < `owner`. In `closed` mode a colleague
has no account to add, so invite them while signup is `first-only`, or switch
to `open` for as long as it takes.

## No route out?

`deploy/offline` builds one archive — the images, this stack and an installer
— for a host with no internet at all: `bundle.sh` where there is a network,
`install.sh` where there is not. TLS there is your own certificate or Caddy's
local CA, never Let's Encrypt. See
[`deploy/offline/README.md`](../offline/README.md).

## Single sign-on, SCIM and the licence

Business and above sign people in through the company's identity provider
(OpenID Connect or SAML 2.0) and let it add and remove them (SCIM 2.0). On
this install that needs two things in `.env`:

- `AXOR_VENDOR_PUBKEY` (and `AXOR_ORG`), which come with the licence. Both the
  plane and the login service check licences with this key.
- The login service's public address. It defaults to
  `https://$CP_HOST/identity`; set `AXOR_IDENTITY_PUBLIC_URL` only if browsers
  reach it somewhere else. Identity providers are given addresses under it:
  the OIDC redirect, the SAML ACS, and the SCIM base `…/identity/scim/v2`.

Then paste the licence in Settings → Billing, or set `AXOR_LICENSE`. The
owner sets up SSO and SCIM in Settings → workspace. `docs/sso-scim.md` walks
through Okta, Entra ID and Google Workspace step by step.

The login service reaches the plane on the compose network
(`AXOR_IDENTITY_PLANE_URL`, `http://backend:8400`). A person the provider
deactivates loses the keys their machines report with, as well as their
membership.

## Two tokens, and which is which

- **`AXOR_API_TOKEN`** — your operator master secret, generated into `.env`. It
  is not how anyone logs in. You paste it once in Settings → auth to mint the
  proxy's ingest key.
- **The ingest key** — a scoped credential the proxy uses to upload run traces
  (`POST /v1/ingest/...`). It can do that and nothing else, so a compromised
  node cannot escalate. Mint it in the UI, then:

```sh
sed -i 's/^AXOR_INGEST_KEY=.*/AXOR_INGEST_KEY=…/' .env
docker compose up -d proxy
```

People who use the product need neither. They register and log in.

## Upgrades

```sh
cd /opt/axor && git pull --ff-only && cd deploy/self-hosted
docker compose pull && docker compose up -d && docker image prune -f
```

`AXOR_TAG` pins the version. Keep it on an exact release once you have users —
then a rollback is the previous tag.

## Backups

The data lives in the `pgdata` and `axordata` volumes. `deploy/prod/backup.sh`
works here too; point `BACKUP_DIR` at somewhere off this disk and run it from
cron. Set it up now, not after the first incident.
