"""Sign in with GitHub / Google.

- users.password_hash becomes nullable: an account made through a provider has
  no password until its owner sets one.
- users.email_verified: set when a provider vouches for the address (and, later,
  by the e-mail verification link).
- oauth_identities: (provider, subject) → user. The subject is the provider's
  stable id, so a renamed GitHub login or a changed address still lands on the
  same account.
- oauth_states: the in-flight sign-ins (state → PKCE verifier, return_to).
- login_codes: the one-time code the browser trades for a session after the
  provider sends it back, so tokens never travel in a URL.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("password_hash", existing_type=sa.String(200), nullable=True)
        batch.add_column(sa.Column("email_verified", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    op.create_table(
        "oauth_identities",
        sa.Column("provider", sa.String(24), primary_key=True),
        sa.Column("subject", sa.String(128), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "oauth_states",
        sa.Column("state_hash", sa.String(64), primary_key=True),
        sa.Column("provider", sa.String(24), nullable=False),
        sa.Column("verifier", sa.String(128), nullable=False),
        sa.Column("return_to", sa.String(1000), nullable=False),
        sa.Column("expires_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "login_codes",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("created", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_ts", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("login_codes")
    op.drop_table("oauth_states")
    op.drop_table("oauth_identities")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("email_verified")
        batch.alter_column("password_hash", existing_type=sa.String(200), nullable=False)
