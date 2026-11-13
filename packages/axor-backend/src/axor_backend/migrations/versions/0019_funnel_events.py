"""funnel_events: did visitors become users — measured, first-party, anonymous.

The product had no way to tell whether a change to sign-up helped: no
analytics, no email, nothing. This table holds a small whitelist of funnel
steps (a visit, a guest session, a first caught lie, a sign-up prompt, a
completed sign-up, claimed guest work, a connected agent), keyed by a random
per-tab id. No IP, no user id, no email. Read only by the operator.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "funnel_events" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "funnel_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.String(40), nullable=False, index=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("event", sa.String(40), nullable=False, index=True),
        sa.Column("detail", sa.String(64), nullable=False, server_default=""),
        sa.Column("dedup_key", sa.String(200), nullable=True, unique=True),
    )


def downgrade() -> None:
    op.drop_table("funnel_events")
