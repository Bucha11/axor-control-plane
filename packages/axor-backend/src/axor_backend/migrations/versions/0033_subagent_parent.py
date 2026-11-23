"""A coding agent's subagent (Claude Code's Agent tool) is a node of its own,
under the session that started it: the parent is kept on its profile."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "parent" not in {c["name"] for c in insp.get_columns("node_profiles")}:
        op.add_column("node_profiles", sa.Column("parent", sa.String(200), nullable=False,
                                                 server_default=""))


def downgrade() -> None:
    op.drop_column("node_profiles", "parent")
