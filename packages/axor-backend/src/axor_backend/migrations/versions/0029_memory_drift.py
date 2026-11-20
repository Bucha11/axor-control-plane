"""The team's memory: versions of what agents were given, and the change log."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "version" not in {c["name"] for c in insp.get_columns("memory_given")}:
        op.add_column("memory_given", sa.Column("version", sa.String(16), nullable=False,
                                                server_default=""))
    if "memory_changes" not in insp.get_table_names():
        op.create_table(
            "memory_changes",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.String(64), nullable=False, index=True),
            sa.Column("repo", sa.String(64), nullable=False, index=True),
            sa.Column("ts", sa.String(40), nullable=False),
            sa.Column("version", sa.String(16), nullable=False),
            sa.Column("previous", sa.String(16), nullable=False, server_default=""),
            sa.Column("doc", sa.Text, nullable=False),
        )


def downgrade() -> None:
    op.drop_table("memory_changes")
    op.drop_column("memory_given", "version")
