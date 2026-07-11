"""Baseline: the launch schema (10 tables), frozen.

Matches axor_backend.storage at the moment migrations were introduced. Future
schema changes are NEW revisions on top — this file never changes again. JSON
payload columns are portable JSON on SQLite and JSONB on Postgres, the same
variant storage.py declares.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("scenario", sa.String(128), nullable=False),
        sa.Column("intervened", sa.Boolean(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("evidence_json", _json(), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(64), nullable=False, index=True),
        sa.Column("node_id", sa.String(128), nullable=False, index=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("line", _json(), nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_events_run_seq"),
    )
    op.create_table(
        "ingest_keys",
        sa.Column("key", sa.String(128), primary_key=True),
    )
    op.create_table(
        "desired_state",
        sa.Column("node_id", sa.String(128), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state_json", _json(), nullable=False),
    )
    op.create_table(
        "reported_state",
        sa.Column("node_id", sa.String(128), primary_key=True),
        sa.Column("applied_version", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(24), nullable=False),
        sa.Column("budget_remaining", sa.Integer(), nullable=True),
        sa.Column("updated_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "facts",
        sa.Column("fact_id", sa.String(128), primary_key=True),
        sa.Column("node_id", sa.String(128), nullable=False, index=True),
        sa.Column("fact_json", _json(), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "pins",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
    )
    op.create_table(
        "api_keys",
        sa.Column("key_id", sa.String(32), primary_key=True),
        sa.Column("hashed_secret", sa.String(64), nullable=False),
        sa.Column("scopes", sa.String(200), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "share_links",
        sa.Column("token", sa.String(64), primary_key=True),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("case_index", sa.Integer(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
    )
    op.create_table(
        "notification_subs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("triggers", sa.String(300), nullable=False),
        sa.Column("debounce_seconds", sa.Float(), nullable=False),
        sa.UniqueConstraint("url", "triggers", name="uq_sub_url_triggers"),
    )


def downgrade() -> None:
    for table in (
        "notification_subs", "share_links", "api_keys", "pins", "facts",
        "reported_state", "desired_state", "ingest_keys", "events", "runs",
    ):
        op.drop_table(table)
