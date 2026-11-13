"""E-mail links: address verification and password reset.

One row per link sent, keyed by the token's hash (the raw token only ever goes
out in the e-mail). Single use: the row is deleted when the link is followed.
The address is kept with the token, so a verification link sent before an
address changed cannot verify the new one.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("purpose", sa.String(16), nullable=False),  # verify | reset
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("expires_ts", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("email_tokens")
