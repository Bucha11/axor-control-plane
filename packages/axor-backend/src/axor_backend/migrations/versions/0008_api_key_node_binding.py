"""Bind an API key to one governed node.

Scopes answer "what may this credential do"; they never answered "who may it do
it AS". Any key with the ``ingest`` scope could post telemetry, facts and health
checks for an arbitrary ``node_id``, so one compromised node could forge a
neighbour's heartbeat — its degradation level and budget — silence that
neighbour's ``node_stale`` page, or replace a DRIFT_DETECTED health verdict with
a clean one. Operator command signing protects the DOWNSTREAM direction; this
column is the upstream half.

Nullable on purpose: NULL is an unbound, fleet-wide operator key, which is what
every key minted before this revision is and what the master token and a human
login remain. Enforcement (``Principal.may_speak_for``) only bites once a key
names a node, so the migration is additive and no existing deployment breaks on
upgrade.

Idempotent (same guard pattern as 0004-0007) and SQLite-safe: adding a NULLABLE
column needs no table rebuild.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("api_keys")}
    if "node_id" in existing:
        return
    op.add_column("api_keys", sa.Column("node_id", sa.String(128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_keys") as batch:
        batch.drop_column("node_id")
