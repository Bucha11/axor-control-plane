"""Personal keys: who minted an api key, so a member can mint and revoke their own."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "created_by" not in {c["name"] for c in insp.get_columns("api_keys")}:
        op.add_column("api_keys", sa.Column("created_by", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("api_keys", "created_by")
