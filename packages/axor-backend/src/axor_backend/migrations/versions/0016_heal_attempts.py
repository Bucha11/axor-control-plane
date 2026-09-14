"""heal_attempts: the commanded excision, and the re-probe that verifies it.

Self-heal was a half loop. The node posted a drift battery, the panel offered
"Self-heal", and the button appended an operator_attestation fact — a record
that someone intervened, next to a context that was never touched. Desired
state carried no `pending_excision`, so the adapter's `take_pending_excision`
returned None and nothing was cut. The panel then said it was "awaiting the
verifying re-probe", and the next battery arrived with nowhere to be folded in.

This table is the heal half, written when an excision actually reaches desired
state (not when a surface offers one), and closed by the first health check
that follows. `resolved` is axor-probe's `heal_outcome`: green only for a
CONSISTENT re-probe — a heal without a verifying re-probe is never rendered as
resolved (ui-spec 8.2.1).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    # a legacy DB stamped at 0001 whose tables were created from current
    # metadata may already carry the table (same pattern as 0004/0005/0006)
    if "heal_attempts" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "heal_attempts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.String(128), nullable=False, index=True),
        sa.Column("excision_id", sa.String(128), nullable=False),
        sa.Column("operator", sa.String(128), nullable=False, server_default=""),
        sa.Column("reason", sa.String(500), nullable=False, server_default=""),
        sa.Column("target_refs", _json(), nullable=False),
        sa.Column("families", _json(), nullable=False),
        sa.Column("requested_ts", sa.String(40), nullable=False),
        # NULL until the verifying re-probe lands. "No verdict yet" and "the
        # re-probe still drifted" are different states and the panel renders
        # them differently, so they are not collapsed into one column.
        sa.Column("reprobe_verdict", sa.String(32), nullable=True),
        sa.Column("resolved", sa.Boolean, nullable=True),
        sa.Column("outcome_ts", sa.String(40), nullable=True),
        sa.Column("org_id", sa.String(64), nullable=False,
                  server_default="public", index=True),
    )


def downgrade() -> None:
    op.drop_table("heal_attempts")
