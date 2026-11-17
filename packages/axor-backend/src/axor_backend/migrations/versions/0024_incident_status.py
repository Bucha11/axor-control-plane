"""What an operator decided about an Incidents item: looked at, kept as a
test, made a rule of, not a problem."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "incident_status" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "incident_status",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("item_id", sa.String(240), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("ts", sa.String(40), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("incident_status")
