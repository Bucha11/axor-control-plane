"""A workspace's plan comes from its signed license, until the license's date."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "tier_expires" not in {c["name"] for c in insp.get_columns("orgs")}:
        op.add_column("orgs", sa.Column("tier_expires", sa.String(40), nullable=True))


def downgrade() -> None:
    op.drop_column("orgs", "tier_expires")
