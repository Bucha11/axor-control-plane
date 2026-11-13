"""What a node is: a production agent, a coding-agent session, or a tree run.

A coding-agent session (Claude Code / Codex through their hooks) and a tree run
report here once. They are metered by the DEVELOPER running them — a session is
not a governed node, or one developer would be a fleet by lunchtime — so the
governed-node count leaves them out and the seat count is drawn from them.
`developer` is an opaque id the client derives (a hash), never an address.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "node_profiles" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "node_profiles",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("node_id", sa.String(200), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("agent", sa.String(40), nullable=False, server_default=""),
        sa.Column("developer", sa.String(64), nullable=False, server_default="", index=True),
        sa.Column("first_ts", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("node_profiles")
