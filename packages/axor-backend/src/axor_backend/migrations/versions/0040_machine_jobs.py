"""Machines that run work for their owner, and the work queued for them.

A machine is a developer's own `axor-wrap worker`, seen through the personal
key it reports with: that key's minter is its owner, decided here and never
taken from what the machine says. A job is work the owner queued from the UI —
a skill eval today — which only that owner's machine is offered: running one
starts a coding agent with permissions bypassed and a shell command of the
owner's choosing, so a plane that could hand it to anyone's machine would be a
way to run code on every developer's laptop.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    tables = sa.inspect(op.get_bind()).get_table_names()
    if "machines" not in tables:
        op.create_table(
            "machines",
            sa.Column("org_id", sa.String(64), primary_key=True),
            sa.Column("machine_id", sa.String(64), primary_key=True),
            sa.Column("owner", sa.String(64), nullable=False, server_default=""),
            sa.Column("key_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("host", sa.String(120), nullable=False, server_default=""),
            sa.Column("repo_label", sa.String(200), nullable=False, server_default=""),
            sa.Column("agents", sa.Text, nullable=False, server_default="[]"),
            sa.Column("last_seen", sa.String(40), nullable=False),
        )
    if "machine_jobs" not in tables:
        op.create_table(
            "machine_jobs",
            sa.Column("org_id", sa.String(64), primary_key=True),
            sa.Column("job_id", sa.String(64), primary_key=True),
            sa.Column("machine_id", sa.String(64), nullable=False, index=True),
            sa.Column("owner", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
            sa.Column("kind", sa.String(40), nullable=False),
            sa.Column("spec", sa.Text, nullable=False),
            sa.Column("state", sa.String(20), nullable=False, index=True),
            sa.Column("cancel_requested", sa.Boolean(), nullable=False,
                      server_default=sa.false()),
            sa.Column("lease_until", sa.String(40), nullable=True),
            sa.Column("progress", sa.Text, nullable=False, server_default="{}"),
            sa.Column("eval_id", sa.String(64), nullable=True),
            sa.Column("error", sa.Text, nullable=False, server_default=""),
            sa.Column("created_ts", sa.String(40), nullable=False, index=True),
            sa.Column("updated_ts", sa.String(40), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("machine_jobs")
    op.drop_table("machines")
