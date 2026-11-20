"""Skills shared for the team: the latest upload and the version turned on."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "team_skills" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "team_skills",
        sa.Column("org_id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(64), primary_key=True),
        sa.Column("latest", sa.Text, nullable=False),
        sa.Column("live", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("team_skills")
