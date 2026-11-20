"""Invitations to an organization: single-use links, for a role."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("invite_id", sa.String(24), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("org_id", sa.String(64), nullable=False, index=True),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
        sa.Column("expires_ts", sa.String(40), nullable=False),
        sa.Column("used_by", sa.String(64), nullable=True),
        sa.Column("used_ts", sa.String(40), nullable=True),
        sa.Column("revoked", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_table("invites")
