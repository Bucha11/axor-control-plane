"""Single sign-on per workspace: its OpenID provider, client, and domains."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_sso",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("issuer", sa.String(500), nullable=False),
        sa.Column("client_id", sa.String(300), nullable=False),
        sa.Column("client_secret", sa.String(500), nullable=False),
        sa.Column("domains", sa.Text, nullable=False),          # JSON list
        sa.Column("enforce", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("default_role", sa.String(24), nullable=False, server_default="member"),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_ts", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("org_sso")
