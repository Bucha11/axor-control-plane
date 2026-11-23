# Air-gapped install

The Control Plane runs on a machine with no route out. Everything it needs is
in one archive you carry in: the images, the compose files, the database
bootstrap and an installer. Nothing phones home — not the licence, not the
product.

## Building the bundle (where there is a network)

From a clone of this repository:

```sh
cd deploy/offline
./bundle.sh 0.5.0                 # a release tag
./bundle.sh 0.5.0 --with-lab      # with Axor Lab (a bigger archive)
```

It writes `axor-offline-0.5.0.tar.gz`: the images as one `docker save` stream,
the stack, `install.sh`, `MANIFEST.txt` with every image digest, and
`SHA256SUMS`. It holds no secrets, so it travels as an ordinary file. Check
`MANIFEST.txt` against the release notes before you carry it in.

## Installing (on the air-gapped host)

The host needs docker with the compose v2 plugin — from your own mirror or the
distribution's packages. The bundle does not carry the engine.

```sh
tar xzf axor-offline-0.5.0.tar.gz
cd axor-offline-0.5.0
sha256sum -c SHA256SUMS
./install.sh axor.internal --cert /etc/ssl/axor.crt --key /etc/ssl/axor.key
```

`install.sh` loads the images, generates every secret on that machine (the
identity signing key, the Postgres password, the operator master token, the
proxy token), writes `.env` at 0600, and starts the stack. Re-running it keeps
an existing `.env`.

Point the hostname at the host — DNS, or `/etc/hosts` on the machines that use
it. Then register the first account: in the default `first-only` mode it
claims the install and becomes the owner.

### TLS

A machine with no route out cannot answer Let's Encrypt, so the offline
Caddyfile has ACME off entirely.

- **Your own certificate** (what most companies want): `--cert` and `--key`.
  The files are copied into `deploy/self-hosted/certs/` and mounted read-only.
  To renew, replace them and `docker compose restart caddy`.
- **No certificate given**: Caddy issues one from its own local CA. Browsers
  warn until that root is trusted; the installer prints how to take it out:

  ```sh
  docker compose exec caddy \
    cat /data/caddy/pki/authorities/local/root.crt > axor-root.crt
  ```

### The licence

Licences are Ed25519-signed files, verified offline against a pinned vendor
key — that is how it already worked, and it is why an air-gapped install
needs nothing special. Either paste it in Settings → Billing, or put it in
`.env`:

```sh
AXOR_VENDOR_PUBKEY=…    # from the vendor, with the licence
AXOR_ORG=…              # the organization the licence names
AXOR_LICENSE={"license":…,"sig":"…"}
docker compose up -d backend identity
```

Set `AXOR_VENDOR_PUBKEY` for the login service too (the same value, already in
`.env`): a workspace's Business plan is what turns single sign-on and SCIM on
there. Licences run to a date, so the next period is a new file — the same
copy-in, no network either way.

## What the install does and does not reach

Nothing in the product calls out on its own. There is no telemetry, no update
check, no licence server. What can leave the network is only what you
configure: webhooks for notifications, an SMTP server for invitations and
password resets, an identity provider for single sign-on. Leave them unset and
the install talks to nobody.

Single sign-on over OIDC or SAML needs a provider the host can reach. On a
truly isolated network that means an internal one (ADFS, Keycloak, an internal
Entra ID federation) — `docs/sso-scim.md` covers the setup, including ADFS,
and `AXOR_IDENTITY_PUBLIC_URL` must be the address the browsers use.

The web app ships every font and asset in its own image: no CDN, no outside
stylesheet, nothing fetched at page load.

## Upgrades

Build the next bundle where there is a network, carry it in, and run its
installer in the same directory tree:

```sh
tar xzf axor-offline-0.6.0.tar.gz
cd axor-offline-0.6.0
sha256sum -c SHA256SUMS
./install.sh axor.internal          # keeps the existing .env and data
```

It loads the new images and restarts the stack; the schema is migrated at
boot. The old images stay on the host, so a rollback is the previous bundle's
directory. Back up first — the data lives in the `pgdata` and `axordata`
volumes and `deploy/prod/backup.sh` works here too.

## When something does not start

| | |
|---|---|
| `pull access denied` or a hanging start | an image is missing from the load: check `docker images` against `MANIFEST.txt`. The override sets `pull_policy: never` so it fails instead of reaching for a registry |
| `does not match its checksums` | the archive was damaged in transit — copy it again |
| the browser warns about the certificate | the local CA's root is not trusted on that machine, or your own certificate does not cover the hostname |
| `exec format error` | the bundle is amd64; the host is not |
