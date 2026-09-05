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
column to a named unique constraint. The ORDER of that rebuild is the part worth
reading: the old table is DROPPED before the new one is created, with the rows
parked in a scratch table meanwhile.

Renaming the old table out of the way and creating the new one alongside it —
the obvious shape, and what this migration did first — cannot work on Postgres.
``ALTER TABLE ... RENAME TO`` does not rename the table's constraints, and
constraint names are unique per schema, so ``uq_sub_url_triggers`` was still
attached to the renamed table when the new one tried to declare it:

    asyncpg.exceptions.DuplicateTableError:
    relation "uq_sub_url_triggers" already exists

0009 gets away with the same shape only because every constraint it declares is
unnamed, and Postgres silently disambiguates auto-generated names (leaving a
``runs_pkey1`` behind). An explicitly named constraint is a hard error — which
made this revision impossible to apply to the production database, on a fresh
one as much as an existing one, since the baseline's ``notification_subs``
carries no org_id and the skip-guard below therefore never skipped.

Existing rows land under the public tenant, so a single-tenant deployment is
unchanged.
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


def _has_org(table: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return "org_id" in {c["name"] for c in inspector.get_columns(table)}


def _rebuild(
    table: str,
    plain_columns: list[sa.Column],
    final_columns: list[sa.schema.SchemaItem],
    carried: list[str],
    indexes: tuple[tuple[str, list[str]], ...] = (),
) -> None:
    """Rebuild `table` with a new shape, keeping every row.

    ``plain_columns`` describe a scratch table holding the OLD shape with no
    named constraints and no indexes — nothing whose name the final table also
    wants. The sequence is park → drop → create → restore → drop scratch, so the
    old table's constraint names are gone from the schema before the new table
    asks for them.
    """
    scratch = f"{table}_pre0010"
    carried_cols = ", ".join(carried)
    op.create_table(scratch, *plain_columns)
    op.execute(f"INSERT INTO {scratch} ({carried_cols}) "  # noqa: S608
               f"SELECT {carried_cols} FROM {table}")
    op.drop_table(table)  # takes its constraints and indexes with it
    op.create_table(table, *final_columns)
    for index_name, index_cols in indexes:
        op.create_index(index_name, table, index_cols)
    op.execute(
        f"INSERT INTO {table} ({carried_cols}, org_id) "  # noqa: S608
        f"SELECT {carried_cols}, '{PUBLIC_ORG}' FROM {scratch}"
    )
    op.drop_table(scratch)


def upgrade() -> None:
    if not _has_org("notification_subs"):
        _rebuild(
            "notification_subs",
            plain_columns=[
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column("url", sa.String(500), nullable=False),
                sa.Column("triggers", sa.String(300), nullable=False),
                sa.Column("debounce_seconds", sa.Float(), nullable=False),
                sa.Column("label", sa.String(100), nullable=False, server_default=""),
                sa.Column("node_pattern", sa.String(200), nullable=False,
                          server_default="*"),
            ],
            final_columns=[
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
            ],
            carried=["id", "url", "triggers", "debounce_seconds", "label",
                     "node_pattern"],
        )

    if not _has_org("dead_letters"):
        _rebuild(
            "dead_letters",
            plain_columns=[
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column("url", sa.String(500), nullable=False),
                sa.Column("payload_json", _JSON, nullable=False),
                sa.Column("error", sa.String(500), nullable=False),
                sa.Column("attempts", sa.Integer(), nullable=False),
                sa.Column("created_ts", sa.String(40), nullable=False),
            ],
            final_columns=[
                sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
                sa.Column("url", sa.String(500), nullable=False),
                sa.Column("payload_json", _JSON, nullable=False),
                sa.Column("error", sa.String(500), nullable=False),
                sa.Column("attempts", sa.Integer(), nullable=False),
                sa.Column("created_ts", sa.String(40), nullable=False),
                _org_column(),
            ],
            carried=["id", "url", "payload_json", "error", "attempts", "created_ts"],
            indexes=(("ix_dead_letters_org_id", ["org_id"]),),
        )


def downgrade() -> None:
    raise NotImplementedError(
        "0010 narrows two global tables to per-tenant ones; going back would "
        "have to decide which tenant's subscriptions become everyone's. "
        "Restore from a backup taken before the upgrade instead."
    )
