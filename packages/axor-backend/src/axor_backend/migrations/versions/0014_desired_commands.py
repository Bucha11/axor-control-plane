"""desired_state.commands_json: the signed command behind each key of the state.

Protocol section 6 says a compromised backend "cannot forge a pause, stop,
injection or attestation". Section 3 said the SSE stream opens with a snapshot
carrying the full desired state, and — because state is LWW — that "reconnect is
trivially correct". Both were implemented, and the second undid the first: a
delta was verified against operator keys, the snapshot carrying the same fields
was not, so anything at all could be pushed as a snapshot instead.

The state is the last-write-wins merge of signed deltas, so what makes it
verifiable is keeping the command that last wrote each key. That is this column:
`{state key -> {version, delta, operator, timestamp, sig}}`. It is bounded by
the number of keys in the lattice, not by how long the node has been running.

Null on upgrade, which is the truth about rows written before it: nothing is
recorded for them, so a signed deployment's adapter refuses their snapshot and
reports `sig_invalid` rather than applying state no operator can be shown to
have commanded. The next command on that node records itself and the node
converges.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # a legacy DB stamped at 0001 whose tables were created from current
    # metadata may already carry it (same pattern as 0004/0005/0006/0011..0013)
    columns = {
        c["name"] for c in sa.inspect(op.get_bind()).get_columns("desired_state")
    }
    if "commands_json" in columns:
        return
    op.add_column(
        "desired_state",
        sa.Column("commands_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("desired_state", "commands_json")
