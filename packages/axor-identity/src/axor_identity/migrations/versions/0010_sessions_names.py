"""A workspace's session policy, when a sign-in began, and the name the
company's identity provider gives a person."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("orgs", sa.Column("session_hours", sa.Integer, nullable=True)),
    ("refresh_tokens", sa.Column("session_ts", sa.String(40), nullable=True)),
    ("users", sa.Column("display_name", sa.String(300), nullable=True)),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    for table, col in _COLUMNS:
        if col.name not in {c["name"] for c in insp.get_columns(table)}:
            op.add_column(table, col)


def downgrade() -> None:
    for table, col in reversed(_COLUMNS):
        op.drop_column(table, col.name)
