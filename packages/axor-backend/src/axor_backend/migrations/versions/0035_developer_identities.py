"""Which person a machine's opaque developer id is: bound when that person's
own key reports a session carrying the id, so a later session from the same
machine on a team key is still theirs. First binding stands; a second person
claiming the same id disputes it, and a disputed id names nobody."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "developer_identities" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "developer_identities",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("developer_id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, index=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="personal_key"),
        sa.Column("ts", sa.String(40), nullable=False),
        sa.Column("disputed_by", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("developer_identities")
