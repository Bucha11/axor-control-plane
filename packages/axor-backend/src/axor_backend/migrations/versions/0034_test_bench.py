"""An agent says whether it is a test bench (a staging copy, no real users):
the plane offers to break its tools on purpose only then. Kept on its profile;
the last word wins."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "test_bench" not in {c["name"] for c in insp.get_columns("node_profiles")}:
        op.add_column("node_profiles", sa.Column("test_bench", sa.Boolean(), nullable=False,
                                                 server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("node_profiles", "test_bench")
