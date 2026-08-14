"""Baseline: users, orgs, memberships, refresh_tokens.

The identity system of record. A user logs in with email + password (argon2),
belongs to one or more orgs with a role, and holds refresh tokens (stored
hashed) that mint short-lived access JWTs.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "orgs",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False, server_default="community"),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("org_id", sa.String(64), nullable=False, index=True),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
        sa.UniqueConstraint("user_id", "org_id", name="uq_membership_user_org"),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("org_id", sa.String(64), nullable=False),
        sa.Column("expires_ts", sa.String(40), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("orgs")
