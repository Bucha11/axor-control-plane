"""Pins name the calls a must_block pin holds.

A run recorded ungoverned denied nothing, so a must_block pin of it had no call
to hold and was reported "unanchored". `must_refuse` ([{node_id, seq}] JSON)
names the call — Replay's "make it a rule" knows which one it is about.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("pins")}
    if "must_refuse" in cols:
        return
    op.add_column("pins", sa.Column("must_refuse", sa.Text, nullable=False, server_default=""))


def downgrade() -> None:
    op.drop_column("pins", "must_refuse")
