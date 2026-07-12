"""Per-node event sequences for multi-node runs (spec v2 Ch.4 §5).

seq is monotonic PER NODE; a tree of N nodes streams N interleaved sequences
through one run, so uniqueness is (run_id, node_id, seq), not (run_id, seq).
Constraint presence is inspected first: a legacy DB stamped at 0001 whose
tables were created from current metadata already carries the new constraint.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def _constraint_names() -> set[str]:
    insp = sa.inspect(op.get_bind())
    return {c["name"] for c in insp.get_unique_constraints("events")}


def upgrade() -> None:
    names = _constraint_names()
    with op.batch_alter_table("events") as batch:
        if "uq_events_run_seq" in names:
            batch.drop_constraint("uq_events_run_seq", type_="unique")
        if "uq_events_run_node_seq" not in names:
            batch.create_unique_constraint(
                "uq_events_run_node_seq", ["run_id", "node_id", "seq"]
            )


def downgrade() -> None:
    names = _constraint_names()
    with op.batch_alter_table("events") as batch:
        if "uq_events_run_node_seq" in names:
            batch.drop_constraint("uq_events_run_node_seq", type_="unique")
        if "uq_events_run_seq" not in names:
            batch.create_unique_constraint("uq_events_run_seq", ["run_id", "seq"])
