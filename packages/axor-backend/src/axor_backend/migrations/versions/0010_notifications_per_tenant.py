"""Scope notification subscriptions and dead letters to their tenant.

0009 fixed the tables whose keys were global; these two are the tables whose
CONTENTS were. A subscription is a webhook URL somebody on call registered, and
a dead letter carries the full payload of a delivery that never arrived — both
are tenant data, and both were process-wide:

* every organization saw every other organization's webhooks on
  ``GET /v1/notifications/subscriptions``;
* ``Notifier.emit`` fanned each trigger out to every subscription regardless of
  which tenant the emitting node belonged to, so one tenant's on-call was paged
  about another tenant's nodes — with the node id, level and permalink in the
  body;
* ``GET /v1/notifications/dead-letters`` returned those payloads to anyone.

The subscription uniqueness constraint gains org_id for the same reason it was
there: two tenants may legitimately register the same URL with the same
triggers, and neither may deduplicate the other away.

Both tables are rebuilt rather than altered in place — SQLite cannot add a
column to a named unique constraint — using the same create/copy/drop/rename
shape as 0009, which is valid on Postgres too. Existing rows land under the
public tenant, so a single-tenant deployment is unchanged.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from axor_backend.tenancy import PUBLIC_ORG
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def _org_column() -> sa.Column:
    return sa.Column(
        "org_id", sa.String(64), nullable=False, server_default=PUBLIC_ORG
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "org_id" not in {c["name"] for c in inspector.get_columns("notification_subs")}:
        op.rename_table("notification_subs", "notification_subs_pre0010")
        op.create_table(
            "notification_subs",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("url", sa.String(500), nullable=False),
            sa.Column("triggers", sa.String(300), nullable=False),
            sa.Column("debounce_seconds", sa.Float(), nullable=False),
            sa.Column("label", sa.String(100), nullable=False, server_default=""),
            sa.Column("node_pattern", sa.String(200), nullable=False,
                      server_default="*"),
            _org_column(),
            sa.UniqueConstraint(
                "org_id", "url", "triggers", "node_pattern",
                name="uq_sub_url_triggers",
            ),
        )
        op.execute(
            "INSERT INTO notification_subs "
            "(id, url, triggers, debounce_seconds, label, node_pattern, org_id) "
            "SELECT id, url, triggers, debounce_seconds, label, node_pattern, "
            f"'{PUBLIC_ORG}' FROM notification_subs_pre0010"
        )
        op.drop_table("notification_subs_pre0010")

    if "org_id" not in {c["name"] for c in inspector.get_columns("dead_letters")}:
        op.rename_table("dead_letters", "dead_letters_pre0010")
        op.create_table(
            "dead_letters",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("url", sa.String(500), nullable=False),
            sa.Column("payload_json", _JSON, nullable=False),
            sa.Column("error", sa.String(500), nullable=False),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("created_ts", sa.String(40), nullable=False),
            _org_column(),
        )
        op.create_index("ix_dead_letters_org_id", "dead_letters", ["org_id"])
        op.execute(
            "INSERT INTO dead_letters "
            "(id, url, payload_json, error, attempts, created_ts, org_id) "
            "SELECT id, url, payload_json, error, attempts, created_ts, "
            f"'{PUBLIC_ORG}' FROM dead_letters_pre0010"
        )
        op.drop_table("dead_letters_pre0010")


def downgrade() -> None:
    raise NotImplementedError(
        "0010 narrows two global tables to per-tenant ones; going back would "
        "have to decide which tenant's subscriptions become everyone's. "
        "Restore from a backup taken before the upgrade instead."
    )
