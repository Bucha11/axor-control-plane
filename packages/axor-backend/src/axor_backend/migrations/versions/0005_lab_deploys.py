"""Lab deploys: accepted axor-cp-deploy/v1 packages (Lab → CP handoff).

One row per accepted package: the validated policy + manifests verbatim
(package_json) and the summary columns the /v1/lab/deploys surface lists.
The regression pins a package creates live in `pins`, labelled
lab:{package_id} — this table is their provenance.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    # a legacy DB stamped at 0001 whose tables were created from current
    # metadata may already carry the table (same pattern as migration 0004)
    if "lab_deploys" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "lab_deploys",
        sa.Column("package_id", sa.String(64), primary_key=True),
        sa.Column("created_ts", sa.String(40), nullable=False),
        sa.Column("kernel", sa.String(120), nullable=False),
        sa.Column("config_hash", sa.String(80), nullable=False),
        sa.Column("parametric_config_hash", sa.String(80), nullable=False),
        sa.Column("pins_created", sa.Integer(), nullable=False),
        sa.Column("manifest_count", sa.Integer(), nullable=False),
        sa.Column("package_json", _json(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("lab_deploys")
