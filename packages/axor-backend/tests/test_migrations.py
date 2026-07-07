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
            "ingest_keys", "alembic_version"} <= tables
    async with engine.connect() as conn:
        rev = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar()
    assert rev == "0001"
    await engine.dispose()


async def test_legacy_create_all_db_is_stamped_and_kept(db_url: str) -> None:
    # A database from a pre-migration build: tables exist, no alembic_version.
    engine = make_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    store = Store(engine)
    await store.pin("run_legacy", "must_block", "kept")

    await init_db(engine)  # stamp + upgrade, not create_table-over-existing

    assert "alembic_version" in await _tables(engine)
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
