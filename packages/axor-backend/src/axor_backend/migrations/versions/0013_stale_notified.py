"""reported_state.stale_notified: the node_stale edge, in the row.

The monitor kept "we have already paged for this node's silence" in a process
set, so a backend restart re-fired node_stale for every node currently past the
window — and `reported_state` rows are never deleted, so a fleet's worth of
decommissioned nodes paged the on-call on every restart, forever. The file's own
contract says a node becomes eligible to fire again ONLY after it heartbeats and
goes stale anew; a restart is not a heartbeat.

Nullable and unset means "not paged since the last heartbeat", which is exactly
what an existing row means on upgrade: a node still silent at that moment pages
once more, and never again until it speaks.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # a legacy DB stamped at 0001 whose tables were created from current
    # metadata may already carry it (same pattern as 0004/0005/0006/0011/0012)
    columns = {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns("reported_state")
    }
    if "stale_notified" in columns:
        return
    op.add_column(
        "reported_state",
        sa.Column("stale_notified", sa.String(40), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reported_state", "stale_notified")
