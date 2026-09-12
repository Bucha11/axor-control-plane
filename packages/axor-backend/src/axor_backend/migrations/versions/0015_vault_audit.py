"""vault_audit: the two custody logs move out of a settings blob.

Both vaults keep an append-only record of privileged actions — which credential
was dispensed for which call, which operator asked for which signature — and
both kept it as one JSON array under a settings key, trimmed to the newest N on
every append. Two consequences, and the first is the serious one.

**The party the log is about could erase it.** Eviction was by COUNT, so writing
rows pushed older rows out. Measured: 520 refused signature requests — refusals
are logged unconditionally, and naming a key id that does not exist costs
nothing — removed every trace of a real signature over "ship v2.0 to
production". The credential log is the same shape: 1019 further dispenses and
the drain that prompted the investigation is gone. An audit trail whose writer
can flush it does not answer the question it exists for.

**And every append rewrote the whole log** — 5.5 ms per dispense at the 1000-row
cap, on the credential hot path, which is also why the cap could not simply be
raised.

Eviction is by AGE now (the deployment's retention window, swept by
`lifecycle.prune_once`), so writing rows cannot accelerate it, and an unset
window means keep forever — the right default for an audit trail. A hard
per-tenant backstop remains so the table cannot grow without bound, and crossing
it logs a warning rather than evicting in silence.

Existing blobs are migrated row by row rather than dropped: they are the only
copy of what the deployment has already done.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_BLOBS = {
    "creds_dispense": "vault_creds/dispense_log/v1",
    "signing": "vault_signing/audit/v1",
}


def upgrade() -> None:
    bind = op.get_bind()
    if "vault_audit" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "vault_audit",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            # "creds_dispense" | "signing" — one table, because the two logs
            # have the same shape and the same lifecycle, and the WALL between
            # the subsystems (spec v2 Ch.5 §3) is about credentials and keys,
            # not about which table their audit rows land in. The reading routes
            # stay separate and each is gated by its own subsystem token.
            sa.Column("kind", sa.String(32), nullable=False, index=True),
            sa.Column("entry_json", sa.JSON(), nullable=False),
            sa.Column("created_ts", sa.String(40), nullable=False),
            sa.Column("org_id", sa.String(64), nullable=False,
                      server_default="public", index=True),
        )

    # Carry the blobs over. They are small by construction (they were capped),
    # so this is bounded work, and they are the only record of what already
    # happened on this deployment.
    settings = sa.table(
        "settings",
        sa.column("key", sa.String),
        sa.column("value", sa.JSON),
        sa.column("org_id", sa.String),
    )
    audit = sa.table(
        "vault_audit",
        sa.column("kind", sa.String),
        sa.column("entry_json", sa.JSON),
        sa.column("created_ts", sa.String),
        sa.column("org_id", sa.String),
    )
    for kind, key in _BLOBS.items():
        for row in bind.execute(
            sa.select(settings.c.value, settings.c.org_id)
            .where(settings.c.key == key)
        ).all():
            stored = row.value
            if isinstance(stored, str):  # a dialect that hands back text
                stored = json.loads(stored)
            for entry in stored or []:
                bind.execute(audit.insert().values(
                    kind=kind, entry_json=entry,
                    created_ts=str(entry.get("ts") or ""),
                    org_id=row.org_id,
                ))
        bind.execute(settings.delete().where(settings.c.key == key))


def downgrade() -> None:
    op.drop_table("vault_audit")
