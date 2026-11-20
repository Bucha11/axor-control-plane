"""Coding-agent setup health checks (axor-eval setup_health)."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "setup_health" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "setup_health",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("check_id", sa.String(64), primary_key=True),
        sa.Column("created_ts", sa.String(40), nullable=False, index=True),
        sa.Column("agent", sa.String(40), nullable=False, server_default=""),
        sa.Column("verdict", sa.String(20), nullable=False, server_default=""),
        sa.Column("doc", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("setup_health")
