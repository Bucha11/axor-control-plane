"""Migrations (launch-readiness §1): fresh DBs reach head via the baseline,
legacy create_all DBs are stamped + upgraded without data loss, and re-running
init_db is idempotent."""
from __future__ import annotations

import pathlib

import pytest
from axor_backend.storage import Store, init_db, make_engine, metadata
from sqlalchemy import inspect, text


async def _tables(engine) -> set[str]:  # noqa: ANN001
    async with engine.connect() as conn:
        return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))


@pytest.fixture
def db_url(tmp_path: pathlib.Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path}/mig.db"


async def test_fresh_db_reaches_head_with_all_tables(db_url: str) -> None:
    engine = make_engine(db_url)
    await init_db(engine)
    tables = await _tables(engine)
    assert {"runs", "events", "desired_state", "reported_state", "facts",
            "pins", "api_keys", "share_links", "notification_subs",
            "ingest_keys", "dead_letters", "settings", "regression_reports",
            "lab_deploys", "probe_reports", "alembic_version"} <= tables
    async with engine.connect() as conn:
        rev = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
        # 0008 binds a key to one node; without the column the plane cannot tell
        # a node's own telemetry from a neighbour's forged heartbeat.
        columns = await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("api_keys")}
        )
    # Head is whatever the newest revision file declares — asserting a literal
    # here only ever measures whether someone remembered to edit this line.
    assert rev == _head_revision()
    assert "node_id" in columns
    await engine.dispose()


def _head_revision() -> str:
    """The newest revision id on disk, read the way alembic reads it."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from axor_backend import storage

    cfg = Config()
    cfg.set_main_option(
        "script_location",
        str(pathlib.Path(storage.__file__).parent / "migrations"),
    )
    return ScriptDirectory.from_config(cfg).get_current_head()


async def test_tenant_tables_are_keyed_by_org(db_url: str) -> None:
    """0009: org_id leads the primary key of every table whose identifier is
    caller-chosen. Before it, org_id only filtered reads — so one tenant could
    take a run_id or node_id out from under another, and two tenants importing
    the same Lab package collided on its deterministic `lab:{trace_id}` pins."""
    engine = make_engine(db_url)
    await init_db(engine)
    async with engine.connect() as conn:
        keys = await conn.run_sync(lambda c: {
            table: inspect(c).get_pk_constraint(table)["constrained_columns"]
            for table in ("runs", "desired_state", "reported_state", "facts",
                          "pins", "lab_deploys", "settings", "ingest_keys",
                          "api_keys", "share_links")
        })
    for table, expected in (
        ("runs", ["org_id", "run_id"]),
        ("desired_state", ["org_id", "node_id"]),
        ("reported_state", ["org_id", "node_id"]),
        ("facts", ["org_id", "fact_id"]),
        ("pins", ["org_id", "run_id"]),
        ("lab_deploys", ["org_id", "package_id"]),
        ("settings", ["org_id", "key"]),
        ("ingest_keys", ["org_id", "key"]),
    ):
        assert keys[table] == expected, table
    # Two deliberate exceptions, both documented in 0009: auth resolves a key_id
    # before the request's org is known, and a share token is an unguessable
    # global capability served on a route that has no principal at all.
    assert keys["api_keys"] == ["key_id"]
    assert keys["share_links"] == ["token"]
    await engine.dispose()


async def test_legacy_create_all_db_is_stamped_and_kept(db_url: str) -> None:
    # A database from a pre-migration build: the BASELINE tables exist, no
    # alembic_version — and no post-baseline tables (they arrive via upgrade).
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        # Strip everything that arrived after the baseline: post-0001 tables,
        # and notification_subs' post-0001 columns/constraint (0003).
        await conn.exec_driver_sql("DROP TABLE dead_letters")
        await conn.exec_driver_sql("DROP TABLE settings")
        await conn.exec_driver_sql("DROP TABLE regression_reports")
        await conn.exec_driver_sql("DROP TABLE notification_subs")
        await conn.exec_driver_sql(
            "CREATE TABLE notification_subs ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, url VARCHAR(500) NOT NULL, "
            "triggers VARCHAR(300) NOT NULL, debounce_seconds FLOAT NOT NULL, "
            "CONSTRAINT uq_sub_url_triggers UNIQUE (url, triggers))"
        )
    store = Store(engine)
    await store.pin("run_legacy", "must_block", "kept")

    await init_db(engine)  # stamp + upgrade, not create_table-over-existing

    tables = await _tables(engine)
    assert "alembic_version" in tables
    assert "dead_letters" in tables  # 0002 applied on top of the stamp
    pins = await store.pinned()
    assert pins == [{"run_id": "run_legacy", "side": "must_block", "label": "kept"}]
    await engine.dispose()


async def test_init_db_is_idempotent(db_url: str) -> None:
    engine = make_engine(db_url)
    await init_db(engine)
    await init_db(engine)  # second boot: upgrade head is a no-op
    assert "runs" in await _tables(engine)
    await engine.dispose()


async def test_retention_prunes_old_runs_with_children(db_url: str) -> None:
    """AXOR_RETENTION_DAYS semantics: runs before the cutoff go, with their
    events, pins and share links; newer runs and plane state stay."""
    engine = make_engine(db_url)
    await init_db(engine)
    store = Store(engine)
    await store.upsert_run("run_old", "n", "s", "2020-01-01T00:00:00+00:00")
    await store.ingest_events("run_old", "n", [
        {"seq": 0, "kind": "claim", "payload": {}}], None)
    await store.pin("run_old", "must_block", "old")
    await store.create_share_link("tok_old", "run_old", 0, "2020-01-01T00:00:00+00:00")
    await store.upsert_run("run_new", "n", "s", "2999-01-01T00:00:00+00:00")

    pruned = await store.prune_runs_older_than("2025-01-01T00:00:00+00:00")
    assert pruned == 1
    assert [r["run_id"] for r in await store.list_runs()] == ["run_new"]
    assert await store.run_events("run_old") == []
    assert await store.pinned() == []
    assert await store.list_share_links() == []
    await engine.dispose()


async def test_dead_letters_survive_a_restart(db_url: str) -> None:
    """The dead-letter log is the record of LOST deliveries — a restart must
    not erase it (that was the in-memory implementation's honest limitation)."""
    engine = make_engine(db_url)
    await init_db(engine)
    await Store(engine).add_dead_letter(
        "http://sink.test/hook", {"trigger": "node_stale", "node_id": "n1"},
        "status 500", 4, "2026-07-08T00:00:00+00:00",
    )
    await engine.dispose()

    engine2 = make_engine(db_url)  # "restart": a fresh engine on the same file
    rows = await Store(engine2).list_dead_letters()
    assert len(rows) == 1
    assert rows[0]["url"] == "http://sink.test/hook"
    assert rows[0]["payload"]["trigger"] == "node_stale"
    assert rows[0]["attempts"] == 4
    await engine2.dispose()


async def test_dead_letter_cap_keeps_only_the_newest(db_url: str) -> None:
    engine = make_engine(db_url)
    await init_db(engine)
    store = Store(engine)
    for i in range(7):
        await store.add_dead_letter(
            f"http://sink.test/{i}", {"trigger": "node_stale"},
            "boom", 4, "2026-07-08T00:00:00+00:00", cap=5,
        )
    rows = await store.list_dead_letters()
    assert len(rows) == 5
    # Newest first; the two oldest were trimmed.
    assert rows[0]["url"] == "http://sink.test/6"
    assert rows[-1]["url"] == "http://sink.test/2"
    await engine.dispose()
