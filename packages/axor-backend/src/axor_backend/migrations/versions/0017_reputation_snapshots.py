"""reputation_snapshots: the cross-session axis, finally reaching the plane.

axor-sentinel is the only component in the product that looks across sessions —
it exists to catch exfiltration staged over dozens of individually normal ones,
which per-session detection structurally cannot see. It runs beside axor-core in
the customer's runtime (the plane must not: enforcement stays local, ui-spec
§12.0), and its cycle writes a versioned ReputationSnapshot.

The plane's half is to RENDER that (ui-spec:416, "topology annotated with
cross-session reputation per node"), and it did not exist: no route took a
snapshot, no table held one, and `GET /v1/plane/topology` answered with posture
and nothing else. So the one thing in the system that survives between sessions
was invisible to the surface built to show it.

ONE row per node, not a series. A snapshot is a complete statement of what that
node's sentinel currently believes; the history behind it is the sentinel's own
append-only evidence sets, on the node, where the facts are. `version` is kept
so a reordered delivery can be refused rather than walking a node's reputation
backwards.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    # a legacy DB stamped at 0001 whose tables were created from current
    # metadata may already carry the table (same pattern as 0004/0005/0006)
    if "reputation_snapshots" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "reputation_snapshots",
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("generated_at", sa.Float, nullable=False),
        sa.Column("received_ts", sa.String(40), nullable=False),
        sa.Column("payload_json", _json(), nullable=False),
        sa.Column("org_id", sa.String(64), nullable=False, server_default="public"),
        sa.PrimaryKeyConstraint("org_id", "node_id", name="pk_reputation_snapshots"),
    )


def downgrade() -> None:
    op.drop_table("reputation_snapshots")
