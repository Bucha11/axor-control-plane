"""Node activity: the per-day record a governed-node invoice is drawn from.

The Control Plane could say how many nodes it had EVER seen — the union of
desired and reported state, with no time in it — and nothing else. That number
is not a fleet size: it never goes down, so a customer who replaced one node
was permanently over their allowance, and it could never have been billed from,
because nobody invoices for a node decommissioned in March.

One row per (tenant, node, UTC day) the node reported in. A day is the coarsest
grain that still supports a peak, and a peak is what a per-node price is
honestly drawn from — a fleet that ran 40 nodes on one day and 5 on the rest is
a 40-node fleet for that day, and the customer can see which day it was.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # a legacy DB stamped at 0001 whose tables were created from current
    # metadata may already carry it (same pattern as 0004/0005/0006)
    if "node_activity" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "node_activity",
        sa.Column("org_id", sa.String(64), nullable=False, server_default="public"),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("day", sa.String(10), nullable=False),
        sa.PrimaryKeyConstraint("org_id", "node_id", "day", name="pk_node_activity"),
    )
    # every read is "this tenant, this date range"
    op.create_index("ix_node_activity_org_day", "node_activity", ["org_id", "day"])


def downgrade() -> None:
    op.drop_index("ix_node_activity_org_day", table_name="node_activity")
    op.drop_table("node_activity")
