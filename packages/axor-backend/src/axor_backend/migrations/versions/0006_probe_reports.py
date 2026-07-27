"""Probe reports: behavioral health checks posted by a node (axor-probe).

One row per battery. History is kept, not overwritten: the panel's drift
sparkline needs ≥2 checks, and a re-probe after a self-heal is a new check —
the heal→verify pair is only readable as a pair if both halves survive.

payload_json is axor-probe's `integration.plane.health_payload` verbatim; the
other columns are the summary the list surface reads.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    # a legacy DB stamped at 0001 whose tables were created from current
    # metadata may already carry the table (same pattern as migrations 0004/0005)
    if "probe_reports" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "probe_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("node_id", sa.String(128), nullable=False, index=True),
        sa.Column("created_ts", sa.String(40), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("overall_verdict", sa.String(32), nullable=False),
        sa.Column("escape_count", sa.Integer(), nullable=False),
        sa.Column("probes_sent", sa.Integer(), nullable=False),
        sa.Column("payload_json", _json(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("probe_reports")
