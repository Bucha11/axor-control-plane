"""Org features (EE): regression history + schedule, notification routing.

- settings: small KV for operator state that must survive restarts (active
  EE license, regression schedule).
- regression_reports: every corpus run leaves a report row (manual and
  scheduled) — the history the EE surface lists.
- notification_subs grows routing fields: label (named channel) and
  node_pattern (glob); uniqueness widens to (url, triggers, node_pattern).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", _json(), nullable=False),
    )
    op.create_table(
        "regression_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_ts", sa.String(40), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("regressed", sa.Integer(), nullable=False),
        sa.Column("escaped", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("safe_to_ship", sa.Boolean(), nullable=False),
        sa.Column("report_json", _json(), nullable=False),
    )
    # batch mode: SQLite rebuilds the table to widen the unique constraint;
    # on Postgres these are plain ALTERs.
    with op.batch_alter_table("notification_subs") as batch:
        batch.add_column(sa.Column(
            "label", sa.String(100), nullable=False, server_default=""
        ))
        batch.add_column(sa.Column(
            "node_pattern", sa.String(200), nullable=False, server_default="*"
        ))
        batch.drop_constraint("uq_sub_url_triggers", type_="unique")
        batch.create_unique_constraint(
            "uq_sub_url_triggers", ["url", "triggers", "node_pattern"]
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_subs") as batch:
        batch.drop_constraint("uq_sub_url_triggers", type_="unique")
        batch.create_unique_constraint("uq_sub_url_triggers", ["url", "triggers"])
        batch.drop_column("node_pattern")
        batch.drop_column("label")
    op.drop_table("regression_reports")
    op.drop_table("settings")
