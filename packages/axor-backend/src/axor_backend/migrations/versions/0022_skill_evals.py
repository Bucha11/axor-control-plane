"""Skill evals: which coding-agent skills help, and what they cost.

One row per report `python -m axor_eval.skills run … --plane` posts: the
report as written (per skill: alone and on top of the others; per arm: solved,
tokens, cost), with the columns the list reads.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "skill_evals" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "skill_evals",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("eval_id", sa.String(64), primary_key=True),
        sa.Column("created_ts", sa.String(40), nullable=False, index=True),
        sa.Column("agent", sa.String(40), nullable=False, server_default=""),
        sa.Column("model", sa.String(120), nullable=False, server_default=""),
        sa.Column("runs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("doc", sa.Text, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("skill_evals")
