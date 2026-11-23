"""SCIM provisioning: one bearer token per workspace, for its identity provider."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scim_tokens",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
        sa.Column("last_used_ts", sa.String(40), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("scim_tokens")
