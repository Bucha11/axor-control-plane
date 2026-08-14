"""Per-organization (tenant) scope: stamp every tenant table with org_id.

Multi-tenancy (tenancy.py): each request carries an org, and the rows it reads
and writes must be that org's and no other. This adds a non-null org_id column
(server_default PUBLIC_ORG) to every tenant table, so existing rows and every
open/no-auth ("public") deployment keep working unchanged — all their data
lives under the single implicit tenant.

Idempotent (a legacy DB whose tables were created from current metadata may
already carry org_id — same guard pattern as 0004/0005/0006) and SQLite-safe:
adding a NOT NULL column WITH a server_default is one of the ALTERs SQLite does
support natively, so no table rebuild is needed on the way up.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from axor_backend.tenancy import PUBLIC_ORG

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Only these tables are org-scoped; everything else (ingest_keys, share_links,
# notification_subs, settings, org_features, dead_letters, multinode_events, …)
# is deliberately left global.
_TENANT_TABLES = (
    "runs", "events", "desired_state", "reported_state", "facts", "pins",
    "probe_reports", "lab_deploys", "regression_reports", "api_keys",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TENANT_TABLES:
        existing = {c["name"] for c in sa.inspect(bind).get_columns(table)}
        if "org_id" in existing:
            continue
        op.add_column(
            table,
            sa.Column(
                "org_id", sa.String(64), nullable=False,
                server_default=PUBLIC_ORG,
            ),
        )
        op.create_index(f"ix_{table}_org_id", table, ["org_id"])


def downgrade() -> None:
    for table in _TENANT_TABLES:
        op.drop_index(f"ix_{table}_org_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("org_id")
