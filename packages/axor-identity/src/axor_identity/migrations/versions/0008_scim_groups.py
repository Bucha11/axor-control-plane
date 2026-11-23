"""SCIM groups: what the provider groups people as, and the role a group gives."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scim_groups",
        sa.Column("group_id", sa.String(40), primary_key=True),
        sa.Column("org_id", sa.String(64), nullable=False, index=True),
        sa.Column("display_name", sa.String(300), nullable=False),
        sa.Column("external_id", sa.String(300), nullable=True),
        sa.Column("members", sa.Text, nullable=False, server_default="[]"),
        sa.Column("role", sa.String(24), nullable=True),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("scim_groups")
