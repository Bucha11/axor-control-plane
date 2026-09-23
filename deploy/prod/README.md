# Production deploy: Control Plane + Axor Lab on one VPS

The cheapest setup that is still a real deployment. It costs about €5–7 a month:
one small x86 VPS, two free static landing pages, and one domain.

```
landing-plane.useaxor.net  → Control Plane landing  (GitHub Pages, this repo's site/)
landing-lab.useaxor.net    → Axor Lab landing       (GitHub Pages, axor-lab's site/)
plane.useaxor.net          → Caddy → frontend  (CP SPA; /v1 backend, /axor proxy, /identity)
lab.useaxor.net            → Caddy → lab-edge  (Lab app + API; /identity)
```

Everything behind Caddy runs from `docker-compose.yml` in this directory:

| Service | What it is |
|---|---|
| `caddy` | The only published ports (80/443). Obtains and renews TLS certificates automatically. |
| `postgres` | One instance with three databases: `axor`, `axor_identity` and `axor_lab`. |
| `backend`, `identity`, `proxy` | The Control Plane, using the `axor-platform` image. |
| `frontend` | CP SPA + reverse proxy (nginx) |
| `lab` | Axor Lab: screen API and the web app in one process. |
| `lab-edge` | nginx in front of the Lab. It applies rate limits and mounts `/identity`. |

**One login for both products.** `identity` issues the access tokens. The CP
backend and the Lab both verify them against the same JWKS, so an account
created in either app works in the other.

## Why this shape

- **No serverless or free-PaaS tier.** Both apps stream live runs over SSE (up
  to an hour per connection). The Lab keeps live run state in memory, and the
  proxy writes traces to disk. Scale-to-zero platforms break each of these.
- **x86, not ARM.** The GHCR images are built for amd64 only.
- **Sizing.** A Hetzner CX22 (2 vCPU, 4 GB, 40 GB) is enough. Freshly booted
  with no load, the whole stack uses about 300 MB of RAM (measured). The
  headroom is for Postgres cache and for reading large runs back:
  `docs/ops-limits.md` measures about 400–550 MB peak for a 200k-event replay.
  The same file has the measured throughput of a single backend.

## First deploy

1. **Server.** Create a VPS running Ubuntu 24.04 (Hetzner CX22 or similar).
   Install Docker and the compose plugin:
   `curl -fsSL https://get.docker.com | sh`. Open only ports 22, 80 and 443 in
   the provider's firewall.
   Do not rely on ufw alone: Docker writes its own iptables rules and bypasses
   it. This compose file publishes only Caddy's ports.
2. **DNS.** Create the records below.

   | Name | Type | Value | Cloudflare proxy |
   |---|---|---|---|
   | `plane` | A | VPS IP | **DNS-only (grey)** |
   | `lab` | A | VPS IP | **DNS-only (grey)** |
   | `landing-plane` | CNAME | `bucha11.github.io` | either |
   | `landing-lab` | CNAME | `bucha11.github.io` | either |

   The app hosts must stay DNS-only: Cloudflare's proxy cuts idle
   connections after about 100 s, which kills SSE streams. Caddy handles TLS
   itself. The landings are static, so proxying them is fine.
3. **Checkout and start.** `bootstrap.sh` generates every secret into `.env`
   (chmod 600, never overwritten on a re-run), pulls the images and starts
   the stack:
   ```sh
   sudo git clone https://github.com/Bucha11/axor-control-plane /opt/axor
   cd /opt/axor/deploy/prod
   ./bootstrap.sh you@example.com     # Let's Encrypt e-mail
   ```
   To fill `.env` by hand instead, copy `.env.example` and follow its comments.
4. **Check.** `docker compose ps` should show every service `healthy`.
   Certificates are issued on the first request to each host.
5. **Ingest key.** Open `https://plane.useaxor.net` and paste `AXOR_API_TOKEN` in
   Settings → auth. Mint an `ingest` API key, set `AXOR_INGEST_KEY` in `.env`,
   and restart the proxy with `docker compose up -d proxy`.
6. **Landings.** In both repos, go to Settings → Pages → Source = "GitHub
   Actions", then set the custom domain: `landing-plane.useaxor.net` in this
   repo and `landing-lab.useaxor.net` in axor-lab. Turn on **Enforce HTTPS**
   once the certificate is issued. Each repo is its own Pages site, and
   GitHub routes by host name, so two subdomains CNAMEd to the same
   `bucha11.github.io` do not overwrite each other. No `CNAME` file is needed:
   an Actions deployment reads the domain from the settings.
   `pages.yml` deploys `site/` on every push to `main` that touches it. For the
   first deploy, run it once by hand (Actions → Pages → Run workflow).
   Also verify `useaxor.net` under your account's Settings → Pages → Verified
   domains (a TXT record). Without it, another repository could claim a
   subdomain that is CNAMEd to GitHub but has no site attached.
7. **Backups.** See below. Set them up now, not after the first incident.

### GHCR package visibility

The CP images (`axor-platform`, `axor-frontend`) are public. The Lab image
(`axor-lab`) is published by axor-lab's `image.yml` workflow. GitHub creates a
new package as **private**. Either make it public (Package settings →
Visibility) or run `docker login ghcr.io` on the server with a read-only PAT.

## Backups

`backup.sh` writes the following to `BACKUP_DIR/<timestamp>/`:
- one `pg_dump -Fc` per database;
- a tarball of the proxy's traces;
- a tarball of the Lab's data directory.

It prunes local copies older than `BACKUP_KEEP_DAYS` and, when
`BACKUP_RCLONE_REMOTE` is set, copies each run off-site. Cloudflare R2's free
tier (10 GB, no egress fees) is a good fit for the off-site copy.

```sh
apt install rclone && rclone config          # add an R2 (S3-compatible) remote
# .env: BACKUP_RCLONE_REMOTE=r2:axor-backups
sudo crontab -e
15 3 * * * /opt/axor/deploy/prod/backup.sh >> /var/log/axor-backup.log 2>&1
```

**Restoring one database** (for example after a bad upgrade):
```sh
docker compose stop backend identity proxy frontend lab lab-edge
docker compose exec -T postgres dropdb -U axor axor_lab
docker compose exec -T postgres createdb -U axor axor_lab
docker compose exec -T postgres pg_restore -U axor -d axor_lab < /var/backups/axor/<ts>/axor_lab.dump
docker compose up -d
```
Use the same steps for `axor` and `axor_identity`. The backend and identity run
their migrations again at boot.

## Upgrades

Upgrade manually on the server:
```sh
cd /opt/axor && git pull --ff-only && cd deploy/prod
./backup.sh
docker compose pull && docker compose up -d && docker image prune -f
```

Or from GitHub with **Actions → Deploy → Run workflow**. You can optionally
give the image tags to roll to. It needs these repository secrets:
`DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` and `DEPLOY_KNOWN_HOSTS` (see
`.github/workflows/deploy.yml`).

Pin exact versions (`AXOR_TAG=0.14.2`, `LAB_TAG=…`) once you have real users.
Then a rollback is just the previous tag plus the pre-upgrade dump.

## Security checklist

Required values are enforced: compose refuses to start without the API token,
the proxy token, the identity signing key and the Lab control token. Also:

- Leave `AXOR_ALLOW_UNSIGNED=0` (the default here) unless the instance is
  deliberately a demo. With it off, set `AXOR_OPERATOR_KEYS`.
- Leave `AXOR_LAB_GUEST_SESSIONS` empty until you have watched `lab-edge`'s
  rate limits under real traffic.
- `.env` must be `chmod 600`. Never commit it (it is gitignored).
- `SECURITY.md` in the repo root has the full hardening list.
