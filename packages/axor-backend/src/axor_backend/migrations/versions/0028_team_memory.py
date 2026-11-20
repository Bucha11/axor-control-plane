"""The team's memory: repositories on coding sessions, what was learned there,
and which session was given what."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    cols = {c["name"] for c in insp.get_columns("node_profiles")}
    if "repo" not in cols:
        op.add_column("node_profiles", sa.Column("repo", sa.String(64), nullable=False,
                                                 server_default=""))
        op.create_index("ix_node_profiles_repo", "node_profiles", ["repo"])
    if "repo_label" not in cols:
        op.add_column("node_profiles", sa.Column("repo_label", sa.String(100), nullable=False,
                                                 server_default=""))
    have = insp.get_table_names()
    if "memory_items" not in have:
        op.create_table(
            "memory_items",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.String(64), nullable=False, index=True),
            sa.Column("repo", sa.String(64), nullable=False, index=True),
            sa.Column("key", sa.String(512), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("doc", sa.Text, nullable=False),
            sa.Column("updated_ts", sa.String(40), nullable=False),
            sa.UniqueConstraint("org_id", "repo", "key", name="uq_memory_items_key"),
        )
    if "memory_given" not in have:
        op.create_table(
            "memory_given",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("org_id", sa.String(64), nullable=False, index=True),
            sa.Column("repo", sa.String(64), nullable=False, index=True),
            sa.Column("node_id", sa.String(200), nullable=False),
            sa.Column("ids", sa.Text, nullable=False),
            sa.Column("ts", sa.String(40), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("memory_given")
    op.drop_table("memory_items")
    op.drop_index("ix_node_profiles_repo", "node_profiles")
    op.drop_column("node_profiles", "repo_label")
    op.drop_column("node_profiles", "repo")
