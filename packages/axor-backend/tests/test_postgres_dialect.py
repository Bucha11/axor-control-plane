"""The other half of "Postgres in production, SQLite for dev".

The whole suite runs on SQLite, and until this file existed the only thing that
ever touched Postgres was a three-route smoke test on the deploy path — which
runs on pushes to main, never on a pull request. So the second dialect was a
claim, not a fact, and the first thing it cost us was a migration that could not
be applied to the production database at all:

    asyncpg.exceptions.DuplicateTableError:
    relation "uq_sub_url_triggers" already exists

Postgres does not rename a table's constraints when the table is renamed, and
constraint names are unique per schema. SQLite has neither property, so the
rebuild pattern in migration 0010 passed every test and would have broken every
Postgres deployment at boot.

These tests run only when AXOR_TEST_POSTGRES_URL points at a database this
process may create schemas in. CI sets it; locally, skipped.

Each test gets its own schema, so they are isolated without needing a database
each — alembic creates the tables inside it and it is dropped afterwards.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import uuid
from collections.abc import AsyncIterator

import pytest
from axor_backend.errors import StaleVersion
from axor_backend.storage import Store, init_db
from axor_backend.tenancy import PUBLIC_ORG, set_current_org
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

POSTGRES_URL = os.environ.get("AXOR_TEST_POSTGRES_URL", "")

pytestmark = [
    pytest.mark.skipif(not POSTGRES_URL, reason="AXOR_TEST_POSTGRES_URL not set"),
    pytest.mark.postgres,
]


@pytest.fixture
async def pg_store() -> AsyncIterator[Store]:
    """A Store on a private Postgres schema, migrated to head."""
    schema = f"t{uuid.uuid4().hex[:12]}"
    admin = create_async_engine(POSTGRES_URL)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await admin.dispose()

    engine = create_async_engine(
        POSTGRES_URL,
        connect_args={"server_settings": {"search_path": schema}},
    )
    store = Store(engine)
    await init_db(engine)
    set_current_org(PUBLIC_ORG)
    try:
        yield store
    finally:
        await engine.dispose()
        admin = create_async_engine(POSTGRES_URL)
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
        set_current_org(PUBLIC_ORG)


def _line(node: str, seq: int, kind: str = "tool_call") -> dict:
    return {"node_id": node, "seq": seq, "kind": kind,
            "schema_version": "1", "payload": {}}


def _script_head() -> str:
    """The head revision alembic itself would migrate to."""
    import axor_backend
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    # `migrations` is a namespace package (no __init__), so it has no __file__;
    # locate it from the package that contains it.
    config = Config()
    config.set_main_option(
        "script_location",
        str(pathlib.Path(axor_backend.__file__).parent / "migrations"),
    )
    return ScriptDirectory.from_config(config).get_current_head()


async def test_the_migration_chain_reaches_head(pg_store: Store) -> None:
    """The fixture already ran it; this asserts the outcome explicitly, because
    the failure it guards against was a hard error partway through the chain
    that rolled the whole thing back and left an empty database."""
    async with pg_store.engine.connect() as conn:
        version = (await conn.execute(
            text("SELECT version_num FROM alembic_version")
        )).scalar()
        tables = {
            r[0] for r in (await conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            ))).all()
        }
    # The literal used to be written here, so every migration broke this test
    # and the fix was to retype the number — which is not what "reaches head"
    # means. Head is whatever the script directory says it is.
    assert version == _script_head()
    assert {"runs", "events", "notification_subs", "dead_letters",
            "node_activity"} <= tables
    # And no scratch table survived the rebuild.
    assert not [t for t in tables if t.endswith(("_pre0009", "_pre0010"))]


async def test_json_columns_are_real_jsonb(pg_store: Store) -> None:
    """The point of running Postgres at all: governance payloads stop being
    opaque TEXT the moment the deployment moves off SQLite."""
    async with pg_store.engine.connect() as conn:
        types = dict((r[0], r[1]) for r in (await conn.execute(text(
            "SELECT table_name || '.' || column_name, data_type "
            "FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND column_name IN ('line', 'evidence_json', 'state_json')"
        ))).all())
    assert types["events.line"] == "jsonb"
    assert types["runs.evidence_json"] == "jsonb"
    assert types["desired_state.state_json"] == "jsonb"


async def test_batched_insert_returns_ids_in_order(pg_store: Store) -> None:
    """`insert().returning()` with executemany is the fast path on both engines;
    the ids it hands back are the audit stream's cursor, so their order is not
    incidental."""
    await pg_store.upsert_run("r", "n", "s", "2026-01-01T00:00:00")
    rows = await pg_store.ingest_events(
        "r", "n", [_line("n", i) for i in range(500)], None,
    )
    ids = [event_id for event_id, _ in rows]
    assert len(ids) == 500
    assert ids == sorted(ids)


async def test_a_trace_reads_back_in_append_order(pg_store: Store) -> None:
    from axor_backend import demo

    await pg_store.upsert_run("ex_tree", demo.TREE_ORCH, "d", "2026-01-01T00:00:00")
    await pg_store.ingest_events("ex_tree", demo.TREE_ORCH, demo.TREE_EVENTS, None)
    authored = [(e["node_id"], e["seq"]) for e in demo.TREE_EVENTS]
    stored = [
        (json.loads(raw)["node_id"], json.loads(raw)["seq"])
        for raw in await pg_store.run_events("ex_tree")
    ]
    assert stored == authored


async def test_concurrent_commands_all_land_on_real_connections(
    pg_store: Store,
) -> None:
    """On SQLite the same test proves less than it looks: writes serialise
    behind one file lock. Here the twelve commands really are on twelve
    connections, so the versioned compare-and-set is doing the work."""
    await pg_store.bump_desired("n1", {"base": 1})
    await asyncio.gather(*[
        pg_store.bump_desired("n1", {f"k{i}": i}) for i in range(12)
    ])
    version, state = await pg_store.get_desired("n1")
    assert state == {"base": 1, **{f"k{i}": i for i in range(12)}}
    assert version == 13


async def test_a_signed_command_is_refused_at_the_wrong_version(
    pg_store: Store,
) -> None:
    await pg_store.bump_desired("n1", {"a": 1})
    await pg_store.bump_desired("n1", {"b": 2})
    with pytest.raises(StaleVersion):
        await pg_store.bump_desired("n1", {"c": 3}, expect_version=1)


async def test_the_dead_letter_cap_is_per_tenant(pg_store: Store) -> None:
    """A NOT IN over a limited subquery — the shape most likely to behave
    differently between the two engines."""
    for i in range(6):
        await pg_store.add_dead_letter(f"http://a/{i}", {"n": i}, "e", 1,
                                       f"t{i}", cap=5, org="org_a")
    for i in range(20):
        await pg_store.add_dead_letter(f"http://b/{i}", {"n": i}, "e", 1,
                                       f"t{i}", cap=5, org="org_b")
    set_current_org("org_a")
    assert len(await pg_store.list_dead_letters()) == 5
    set_current_org("org_b")
    assert len(await pg_store.list_dead_letters()) == 5


async def test_concurrent_idempotent_writes_do_not_raise(pg_store: Store) -> None:
    """Check-then-act plus an IntegrityError catch. SQLite serialises writers,
    so this is the engine where the race is actually reachable."""
    await asyncio.gather(*[
        pg_store.upsert_run("racy", "n", "s", "2026-01-01T00:00:00")
        for _ in range(8)
    ])
    assert (await pg_store.get_run("racy"))["run_id"] == "racy"

    await asyncio.gather(*[pg_store.pin("racy", "must_block", "l") for _ in range(8)])
    assert len(await pg_store.pinned()) == 1

    await asyncio.gather(*[
        pg_store.append_fact("n", {"fact_id": "f1"}, "2026-01-01T00:00:00")
        for _ in range(8)
    ])
    assert len(await pg_store.node_facts("n")) == 1


async def test_the_node_meter_is_idempotent_on_postgres(pg_store: Store) -> None:
    """The meter's whole shape is a composite primary key doing the deduping —
    a heartbeat every ten seconds must write one row a day. SQLite tolerates
    plenty that Postgres refuses, and the constraint is the feature here, so it
    is exercised on the engine a deployment actually runs."""
    for _ in range(10):
        await pg_store.record_node_activity("n1", "2026-05-01")
    await pg_store.record_node_activity("n2", "2026-05-01")
    await pg_store.record_node_activity("n1", "2026-05-02")
    usage = await pg_store.node_usage("2026-05-01", "2026-05-31")
    assert usage["peak_nodes"] == 2
    assert usage["peak_day"] == "2026-05-01"
    assert usage["distinct_nodes"] == 2
    assert usage["days"] == [{"day": "2026-05-01", "nodes": 2},
                             {"day": "2026-05-02", "nodes": 1}]


async def test_the_meter_range_is_a_date_range_not_a_string_prefix(
    pg_store: Store,
) -> None:
    """`day` is a CHAR-shaped column and the range is compared as text, which is
    only correct because ISO dates sort as dates. Postgres collation is not
    SQLite's, so the assumption is checked here rather than assumed."""
    for day in ("2025-12-31", "2026-01-01", "2026-01-31", "2026-02-01"):
        await pg_store.record_node_activity("n1", day)
    january = await pg_store.node_usage("2026-01-01", "2026-01-31")
    assert [d["day"] for d in january["days"]] == ["2026-01-01", "2026-01-31"]


async def test_attestation_facts_filter_inside_the_database(
    pg_store: Store,
) -> None:
    """`attestation_facts` filters on two keys INSIDE `fact_json` rather than
    loading the tenant's whole fact log and sorting it out in Python — facts are
    also where every node's degradation transition and heat crossing land, so
    that log grows with fleet chatter, not with operator actions.

    SQLAlchemy renders that filter as `json_extract` on SQLite and `->>` on
    JSONB. Two dialects, one expression: the assumption is checked here rather
    than assumed, which is the whole reason this file exists.
    """
    await pg_store.append_fact("n1", {
        "fact_id": "att", "fact_type": "operator_attestation",
        "run_id": "run_a", "covers": ["v_ext_1"], "operator": "op",
        "reason": "checked",
    }, "2026-05-01T00:00:00Z")
    await pg_store.append_fact("n1", {
        "fact_id": "att_other_run", "fact_type": "operator_attestation",
        "run_id": "run_b", "covers": ["v_ext_1"], "operator": "op",
        "reason": "checked",
    }, "2026-05-01T00:00:01Z")
    await pg_store.append_fact("n1", {
        "fact_id": "deg", "fact_type": "degradation_transition",
        "run_id": "run_a", "severity": 1,
    }, "2026-05-01T00:00:02Z")

    rows = await pg_store.attestation_facts("run_a")
    assert [f["fact_id"] for f in rows] == ["att"]
