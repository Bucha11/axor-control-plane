"""A note an operator signed in the browser (``{"payload", "operator", "sig"}``):
a sidecar with operator keys takes it as the developer's words; one no operator
signed it gives its agent as outside text."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "signed" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("pair_notes")}:
        op.add_column("pair_notes", sa.Column("signed", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("pair_notes", "signed")
