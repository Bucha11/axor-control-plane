"""Persist notification dead letters.

The dead-letter log is the notification channel's honesty ledger; keeping it
in memory meant a restart erased the evidence that deliveries were lost. Capped
at insert time (storage.add_dead_letter), so no unbounded growth.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "dead_letters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("payload_json", sa.JSON().with_variant(JSONB(), "postgresql"),
                  nullable=False),
        sa.Column("error", sa.String(500), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("dead_letters")
