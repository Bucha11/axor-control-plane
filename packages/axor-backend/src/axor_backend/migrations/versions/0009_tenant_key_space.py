"""Make the tenant key space actually tenant-scoped.

Migration 0007 added ``org_id`` to the tenant tables and scoped every READ by
it, but left the single-column primary keys alone. So the *filter* was
per-tenant while the *key space* stayed global: organization B inserting a
``run_id`` or ``node_id`` organization A already used got an IntegrityError
(the existence check runs WITH the org filter, the insert without), and for a
Lab handoff — whose pins are the deterministic ``lab:{trace_id}`` — two tenants
importing the same package collide by construction.

This revision moves ``org_id`` into the primary key of every table whose
identifier is caller-chosen, and scopes the three tables 0007 deliberately left
global but whose CONTENTS are per-tenant:

* ``settings`` — the EE licence, the regression schedule, and both federation
  vaults (enrolled tool credentials, signing-key seeds, the signing audit).
  A global KV meant one tenant's ``get_setting`` returned another's vault.
* ``ingest_keys`` — client-chosen idempotency keys; a collision across tenants
  silently drops the other tenant's batch as a "duplicate".
* ``share_links`` — gains ``org_id`` but keeps ``token`` as its sole key: a
  token is an unguessable global capability and ``GET /v1/share/{token}`` is
  served without auth, so there is no principal to take an org from. The column
  is what lets that open route adopt the link's tenant before it looks the case
  up; without it the lookup ran under the public tenant and 404'd every link an
  identity user had created.

``api_keys`` deliberately keeps its single-column key: auth resolves a key_id
BEFORE the request's org is known, so that lookup is global by design and the
row carries the org it was minted under.

Portability: SQLite cannot alter a primary key in place, so every rebuilt table
goes through create-new / copy / drop / rename, which is also valid on
Postgres. Redundant single-column ``org_id`` indexes from 0007 are dropped
where ``org_id`` becomes the leading primary-key column — the primary key
already serves the tenant filter.
"""
from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from alembic import op
from axor_backend.tenancy import PUBLIC_ORG
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def _org_column() -> sa.Column:
    """A fresh org_id Column per table (a Column instance binds to one table)."""
    return sa.Column(
        "org_id", sa.String(64), nullable=False, server_default=PUBLIC_ORG
    )


def _rebuild(
    table: str, columns: list[Any], select_columns: list[str],
    *, drop_org_index: bool = True,
) -> None:
    """Recreate `table` with a new key, copying every existing row.

    `select_columns` are the columns read from the old table, in the order the
    new one declares them; a column the old table does not have (org_id on a
    table this revision is adding it to) is supplied as a literal instead.
    """
    if drop_org_index:
        # 0007 created ix_{table}_org_id; once org_id leads the primary key the
        # index is redundant, and its NAME would collide with the rebuilt table
        # until the old table is dropped. Absent on a DB that never had it.
        bind = op.get_bind()
        names = {ix["name"] for ix in sa.inspect(bind).get_indexes(table)}
        if f"ix_{table}_org_id" in names:
            op.drop_index(f"ix_{table}_org_id", table_name=table)
    op.rename_table(table, f"{table}_pre0009")
    op.create_table(table, *columns)
    cols = ", ".join(select_columns)
    target = ", ".join(c.name for c in columns if isinstance(c, sa.Column))
    op.execute(
        f"INSERT INTO {table} ({target}) SELECT {cols} FROM {table}_pre0009"  # noqa: S608
    )
    op.drop_table(f"{table}_pre0009")


def upgrade() -> None:
    bind = op.get_bind()
    has_org = {
        t: "org_id" in {c["name"] for c in sa.inspect(bind).get_columns(t)}
        for t in ("settings", "ingest_keys", "share_links")
    }

    # ── caller-chosen ids: org_id joins the primary key ──────────────────────
    _rebuild("runs", [
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("scenario", sa.String(128), nullable=False),
        sa.Column("intervened", sa.Boolean(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("evidence_json", _JSON, nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
        _org_column(),
        sa.PrimaryKeyConstraint("org_id", "run_id"),
    ], ["run_id", "node_id", "scenario", "intervened", "completed",
        "evidence_json", "created_ts", "org_id"])

    _rebuild("desired_state", [
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state_json", _JSON, nullable=False),
        _org_column(),
        sa.PrimaryKeyConstraint("org_id", "node_id"),
    ], ["node_id", "version", "state_json", "org_id"])

    _rebuild("reported_state", [
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("applied_version", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(24), nullable=False),
        sa.Column("budget_remaining", sa.Integer(), nullable=True),
        sa.Column("updated_ts", sa.String(40), nullable=False),
        _org_column(),
        sa.PrimaryKeyConstraint("org_id", "node_id"),
    ], ["node_id", "applied_version", "level", "budget_remaining",
        "updated_ts", "org_id"])

    _rebuild("facts", [
        sa.Column("fact_id", sa.String(128), nullable=False),
        sa.Column("node_id", sa.String(128), nullable=False),
        sa.Column("fact_json", _JSON, nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
        _org_column(),
        sa.PrimaryKeyConstraint("org_id", "fact_id"),
    ], ["fact_id", "node_id", "fact_json", "created_ts", "org_id"])
    op.create_index("ix_facts_node_id", "facts", ["node_id"])

    _rebuild("pins", [
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        _org_column(),
        sa.PrimaryKeyConstraint("org_id", "run_id"),
    ], ["run_id", "side", "label", "org_id"])

    _rebuild("lab_deploys", [
        sa.Column("package_id", sa.String(64), nullable=False),
        sa.Column("created_ts", sa.String(40), nullable=False),
        sa.Column("kernel", sa.String(120), nullable=False),
        sa.Column("config_hash", sa.String(80), nullable=False),
        sa.Column("parametric_config_hash", sa.String(80), nullable=False),
        sa.Column("pins_created", sa.Integer(), nullable=False),
        sa.Column("manifest_count", sa.Integer(), nullable=False),
        sa.Column("package_json", _JSON, nullable=False),
        _org_column(),
        sa.PrimaryKeyConstraint("org_id", "package_id"),
    ], ["package_id", "created_ts", "kernel", "config_hash",
        "parametric_config_hash", "pins_created", "manifest_count",
        "package_json", "org_id"])

    # ── tables 0007 left global whose contents are per-tenant ───────────────
    _rebuild("settings", [
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", _JSON, nullable=False),
        _org_column(),
        sa.PrimaryKeyConstraint("org_id", "key"),
    ], ["key", "value"] + (["org_id"] if has_org["settings"] else [f"'{PUBLIC_ORG}'"]),
        drop_org_index=False)

    _rebuild("ingest_keys", [
        sa.Column("key", sa.String(128), nullable=False),
        _org_column(),
        sa.PrimaryKeyConstraint("org_id", "key"),
    ], ["key"] + (["org_id"] if has_org["ingest_keys"] else [f"'{PUBLIC_ORG}'"]),
        drop_org_index=False)

    # share_links keeps `token` as its key — only the tenant column is added.
    if not has_org["share_links"]:
        op.add_column("share_links", _org_column())
        op.create_index("ix_share_links_org_id", "share_links", ["org_id"])

    # ── events: the uniqueness that guards append-only is per tenant too ─────
    with op.batch_alter_table("events") as batch:
        batch.drop_constraint("uq_events_run_node_seq", type_="unique")
        batch.create_unique_constraint(
            "uq_events_run_node_seq", ["org_id", "run_id", "node_id", "seq"]
        )


def downgrade() -> None:
    raise NotImplementedError(
        "0009 narrows a global key space to a per-tenant one; going back would "
        "have to pick which tenant's row wins every collision it created. "
        "Restore from a backup taken before the upgrade instead."
    )
