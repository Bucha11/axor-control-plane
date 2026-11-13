"""guest_sessions: a hosted visitor works before signing up, and keeps it.

The hosted plane answered every anonymous request with 401 while its home page
offered "no account" — so a visitor saw a shell and nothing behind it. A guest
session is a short-lived bearer mapped to a throwaway org: the visitor works in
it like a tenant, and signing up claims that org's rows into the account
(the same move the Lab makes). Only the token's HASH is stored. Unclaimed guest
orgs are swept a day after they expire.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "guest_sessions" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "guest_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("org_id", sa.String(64), nullable=False, index=True),
        sa.Column("created_ts", sa.String(40), nullable=False),
        sa.Column("expires_ts", sa.String(40), nullable=False),
        sa.Column("claimed_by", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("guest_sessions")
