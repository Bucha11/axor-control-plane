"""Pair sessions by invitation: whom the developer invited, and teammates' requests to pair."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "invited" not in {c["name"] for c in insp.get_columns("pair_sessions")}:
        op.add_column("pair_sessions", sa.Column("invited", sa.Text, nullable=False,
                                                 server_default="[]"))
    if "pair_requests" not in insp.get_table_names():
        op.create_table(
            "pair_requests",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.String(64), nullable=False, index=True),
            sa.Column("node_id", sa.String(128), nullable=False, index=True),
            sa.Column("by", sa.String(255), nullable=False),
            sa.Column("developer", sa.String(64), nullable=False, index=True),
            sa.Column("ts", sa.String(40), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("decided_ts", sa.String(40), nullable=True),
            sa.Column("token", sa.String(64), nullable=True),
            sa.Column("told", sa.Boolean, nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    op.drop_table("pair_requests")
    op.drop_column("pair_sessions", "invited")
