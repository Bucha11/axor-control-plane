"""Pair sessions and the notes sent into a session's agent."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = sa.inspect(op.get_bind()).get_table_names()
    if "pair_sessions" not in have:
        op.create_table(
            "pair_sessions",
            sa.Column("token", sa.String(64), primary_key=True),
            sa.Column("org_id", sa.String(64), nullable=False, index=True),
            sa.Column("node_id", sa.String(128), nullable=False),
            sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
            sa.Column("created_ts", sa.String(40), nullable=False),
            sa.Column("expires_ts", sa.String(40), nullable=False),
            sa.Column("closed_ts", sa.String(40), nullable=True),
            sa.Column("outside", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("guests", sa.Text, nullable=False, server_default="[]"),
        )
    if "pair_notes" not in have:
        op.create_table(
            "pair_notes",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.String(64), nullable=False, index=True),
            sa.Column("node_id", sa.String(128), nullable=False, index=True),
            sa.Column("token", sa.String(64), nullable=False),
            sa.Column("by", sa.String(255), nullable=False, server_default=""),
            sa.Column("outside", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("text", sa.Text, nullable=False),
            sa.Column("ts", sa.String(40), nullable=False),
            sa.Column("delivered_ts", sa.String(40), nullable=True),
            sa.Column("delivered_at", sa.String(128), nullable=True),
        )


def downgrade() -> None:
    op.drop_table("pair_notes")
    op.drop_table("pair_sessions")
