"""Ending a session or a membership at once (QA-154, QA-240): the session
(refresh-token family) each refresh token belongs to, a membership epoch per
person and workspace, and the recent revocations the control plane reads."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "family" not in {c["name"] for c in insp.get_columns("refresh_tokens")}:
        op.add_column("refresh_tokens", sa.Column("family", sa.String(40), nullable=True))
        op.create_index("ix_refresh_tokens_family", "refresh_tokens", ["family"])
    tables = insp.get_table_names()
    if "token_epochs" not in tables:
        op.create_table(
            "token_epochs",
            sa.Column("user_id", sa.String(64), primary_key=True),
            sa.Column("org_id", sa.String(64), primary_key=True),
            sa.Column("epoch", sa.Integer, nullable=False),
        )
    if "revocations" not in tables:
        op.create_table(
            "revocations",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("kind", sa.String(8), nullable=False),
            sa.Column("key", sa.String(64), nullable=False),
            sa.Column("epoch", sa.Integer, nullable=True),
            sa.Column("reason", sa.String(16), nullable=False),
            sa.Column("ts", sa.Integer, nullable=False),
        )
        op.create_index("ix_revocations_key", "revocations", ["key"])
        op.create_index("ix_revocations_ts", "revocations", ["ts"])


def downgrade() -> None:
    op.drop_table("revocations")
    op.drop_table("token_epochs")
    op.drop_index("ix_refresh_tokens_family", "refresh_tokens")
    op.drop_column("refresh_tokens", "family")
