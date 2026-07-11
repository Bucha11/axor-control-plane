# Runbook — backup, restore, upgrade (self-hosted compose)

## Backup (two things, both cheap)
1. **Postgres** (system of record): `docker compose exec postgres pg_dump -U axor axor | gzip > axor-$(date +%F).sql.gz`
2. **Proxy traces** (portable artifacts): the `axordata` volume →
   `docker run --rm -v axor-control-plane_axordata:/d -v $PWD:/out alpine tar czf /out/traces-$(date +%F).tgz /d/traces`

Not needed: the taint graph (derived — rebuilt from the event log at boot),
share links / subscriptions (rows in Postgres, covered by pg_dump).

## Restore
Fresh stack → `docker compose up -d postgres` → `gunzip -c dump.sql.gz |
docker compose exec -T postgres psql -U axor axor` → `docker compose up -d`.
The backend stamps/upgrades the schema at boot (alembic) and rehydrates the
graph, permalinks and subscriptions from the restored rows.

## Upgrade
1. Back up first (above). 2. `git pull && docker compose build && docker
compose up -d`. Schema migrates automatically at boot; a pre-migration
database is stamped at the baseline then upgraded — data kept.
Rollback = previous image tag + the pre-upgrade dump. Downgrade migrations
exist but restoring the dump is the honest, tested path.

## When something's wrong
- Backend logs: `docker compose logs backend` — boot prints loud warnings if
  running open (no token / unsigned commands). `AXOR_LOG_JSON=1` for
  machine-shippable lines; `SENTRY_DSN` to ship errors.
- Node went quiet: node_stale webhook fires after 3T silence (Settings →
  notifications); check the node's own adapter logs — the plane is advisory,
  the agent never depends on the backend being up.
- Known limits under load: docs/ops-limits.md.
