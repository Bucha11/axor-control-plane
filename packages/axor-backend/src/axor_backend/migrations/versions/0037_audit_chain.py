"""The custody logs as a hash chain: each row carries the hash of the row
before it in its log (org, kind) and its own, so a row edited or removed from
the middle shows. Rows written before this migration stay unchained; the
chain starts at the first row written after it."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("vault_audit")}
    if "prev_hash" not in have:
        op.add_column("vault_audit", sa.Column("prev_hash", sa.String(64), nullable=True))
    if "row_hash" not in have:
        op.add_column("vault_audit", sa.Column("row_hash", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("vault_audit", "row_hash")
    op.drop_column("vault_audit", "prev_hash")
