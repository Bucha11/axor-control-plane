"""Settings: a revision column, so read-modify-write stops losing writes.

Every entry in this KV is a whole blob that callers load, change one field of,
and store back — the federation vault's credentials, its signing keys, its
sign-request audit, the regression schedule. The load and the store were two
transactions on two connections, so a second request that loaded the same old
blob in between erased the first write entirely. Not one field: the whole entry.

Measured before this: twenty concurrent enrollments left ONE credential (all
twenty answered 200), a revoke racing a rotate left the credential live, and
twenty-five signatures left ONE audit row for twenty-five issued signatures.

`revision` makes the compare-and-set this table already needed possible, and it
is the same pattern `desired_state.version` has carried since the baseline:
read it, write with `WHERE revision = the one I read`, retry when zero rows
change. Existing rows start at 0.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # a legacy DB stamped at 0001 whose tables were created from current
    # metadata may already carry it (same pattern as 0004/0005/0006/0011)
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("settings")}
    if "revision" in columns:
        return
    op.add_column(
        "settings",
        sa.Column("revision", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("settings", "revision")
