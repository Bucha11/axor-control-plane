"""Agents of a pair talk to each other: a note can come from another agent
session (``from_node``), carries what the sender's taint ledger said about its
text (``taint``), and may be held for the receiving developer's OK (``held``,
``dismissed_ts``, ``told``)."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("from_node", lambda: sa.Column("from_node", sa.String(128), nullable=True)),
    ("taint", lambda: sa.Column("taint", sa.Text, nullable=False, server_default="[]")),
    ("held", lambda: sa.Column("held", sa.Boolean, nullable=False, server_default=sa.false())),
    ("dismissed_ts", lambda: sa.Column("dismissed_ts", sa.String(40), nullable=True)),
    ("told", lambda: sa.Column("told", sa.Boolean, nullable=False, server_default=sa.false())),
)


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("pair_notes")}
    for name, column in _COLUMNS:
        if name not in have:
            op.add_column("pair_notes", column())


def downgrade() -> None:
    for name, _ in reversed(_COLUMNS):
        op.drop_column("pair_notes", name)
