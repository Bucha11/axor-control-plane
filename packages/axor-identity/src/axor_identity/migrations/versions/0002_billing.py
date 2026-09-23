"""Billing: one Paddle subscription per org, applied webhook events, checkouts.

The org's `tier` stays the entitlement every service reads; these tables are
how the billing webhook decides it (see axor_identity.billing).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "billing_subscriptions",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("customer_id", sa.String(64), nullable=True, index=True),
        sa.Column("subscription_id", sa.String(64), nullable=True, unique=True),
        sa.Column("price_id", sa.String(64), nullable=True),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("current_period_end", sa.String(40), nullable=True),
        sa.Column("scheduled_change", sa.String(40), nullable=True),
        sa.Column("last_event_at", sa.String(40), nullable=False),
        sa.Column("updated_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "billing_events",
        sa.Column("event_id", sa.String(64), primary_key=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("received_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "billing_checkouts",
        sa.Column("transaction_id", sa.String(64), primary_key=True),
        sa.Column("org_id", sa.String(64), nullable=False, index=True),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("return_url", sa.String(2000), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("billing_checkouts")
    op.drop_table("billing_events")
    op.drop_table("billing_subscriptions")
