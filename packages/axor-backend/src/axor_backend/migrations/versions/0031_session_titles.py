"""A coding session's title: the developer's first request in it, as the screens name it."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "title" not in {c["name"] for c in insp.get_columns("node_profiles")}:
        op.add_column("node_profiles", sa.Column("title", sa.String(120), nullable=False,
                                                 server_default=""))


def downgrade() -> None:
    op.drop_column("node_profiles", "title")
