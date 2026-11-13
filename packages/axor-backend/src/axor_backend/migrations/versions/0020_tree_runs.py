"""Agent tree runs (axor-wrap `tree run`): one snapshot per run, per org.

The tree run posts its state as it goes — stages, nodes with their agent and
model, the context items that flowed between them (pushed, withheld as
untrusted, cut by an operator). The snapshot is replaced, not appended: the run
is the unit, and its trace lives with each node's own runs.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # a database built by create_all already has it (the legacy-stamp path)
    if "tree_runs" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "tree_runs",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("tree_run", sa.String(64), primary_key=True),
        sa.Column("updated_ts", sa.String(40), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("doc", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("tree_runs")
