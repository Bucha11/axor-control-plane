"""Operators' signing keys made in their browsers (WebCrypto, non-extractable):
the plane keeps only the public half, bound to the account. A key is trusted
by machines once an operator they already trust endorses it (``endorsement``,
signed); revoking one is the plane's word, and narrows."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "operator_keys" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "operator_keys",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("operator_id", sa.String(200), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, index=True),
        sa.Column("pubkey", sa.String(64), nullable=False),
        sa.Column("label", sa.String(100), nullable=False, server_default=""),
        sa.Column("created_ts", sa.String(40), nullable=False),
        sa.Column("revoked_ts", sa.String(40), nullable=True),
        sa.Column("endorsement", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("operator_keys")
