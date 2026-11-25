"""The audit trail is a hash chain: whole when untouched; an edited row, a row
removed from the middle or one slipped in shows; retention pruning the oldest
end does not break it; the compliance pack carries the head."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend import audit_chain
from axor_backend.app import create_app
from axor_backend.storage import vault_audit
from axor_backend.tenancy import PUBLIC_ORG, set_current_org
from sqlalchemy import delete, insert, select, update

H = {"Authorization": "Bearer master-chain"}


@pytest.fixture
async def app(tmp_path: pathlib.Path):  # noqa: ANN201
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/c.db",
                     operator_keys={}, allow_unsigned=True, api_token="master-chain")
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ANN001
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c:
        yield c


async def _acts(app, n: int) -> list[int]:  # noqa: ANN001
    store = app.state.store
    set_current_org(PUBLIC_ORG)
    for i in range(n):
        await store.add_vault_audit("control", {"action": "pause", "target": f"n{i}",
                                                "ts": f"2026-11-2{i}T00:00:00+00:00"},
                                    f"2026-11-2{i}T00:00:00+00:00")
    return [r["id"] for r in await store.vault_audit_chain("control")]


async def _verify(client: httpx.AsyncClient) -> dict:
    r = await client.get("/v1/audit/verify", headers=H)
    assert r.status_code == 200, r.text
    return r.json()["chains"]["control"]


async def test_whole_then_an_edit_shows(app, client: httpx.AsyncClient) -> None:  # noqa: ANN001
    ids = await _acts(app, 4)
    whole = await _verify(client)
    assert whole["ok"] and whole["rows"] == 4 and whole["head"]["id"] == ids[-1]
    async with app.state.store.engine.begin() as conn:
        await conn.execute(update(vault_audit).where(vault_audit.c.id == ids[1]).values(
            entry_json={"action": "resume", "target": "n1", "ts": "2026-11-21T00:00:00+00:00"}))
    broken = await _verify(client)
    assert not broken["ok"] and broken["broken_at"] == ids[1] and "changed" in broken["reason"]


async def test_a_removal_from_the_middle_shows(app, client: httpx.AsyncClient) -> None:  # noqa: ANN001
    ids = await _acts(app, 4)
    async with app.state.store.engine.begin() as conn:
        await conn.execute(delete(vault_audit).where(vault_audit.c.id == ids[2]))
    broken = await _verify(client)
    assert not broken["ok"] and broken["broken_at"] == ids[3] and "removed" in broken["reason"]


async def test_a_row_slipped_in_shows(app, client: httpx.AsyncClient) -> None:  # noqa: ANN001
    await _acts(app, 2)
    async with app.state.store.engine.begin() as conn:
        await conn.execute(insert(vault_audit).values(
            kind="control", entry_json={"action": "brake_off"}, created_ts="2026-11-29",
            org_id=PUBLIC_ORG, prev_hash="0" * 64, row_hash="1" * 64))
    assert not (await _verify(client))["ok"]


async def test_pruning_the_oldest_end_keeps_it_whole(app, client: httpx.AsyncClient) -> None:  # noqa: ANN001
    ids = await _acts(app, 4)
    set_current_org(PUBLIC_ORG)
    await app.state.store.prune_vault_audit_older_than("2026-11-22T00:00:00+00:00")
    after = await _verify(client)
    assert after["ok"] and after["rows"] == 2 and after["anchor"]["id"] == ids[2]


def test_the_hash_binds_every_part() -> None:
    base = audit_chain.row_hash("p", "o", "control", "t", {"a": 1})
    assert base != audit_chain.row_hash("p", "o", "control", "t", {"a": 2})
    assert base != audit_chain.row_hash("q", "o", "control", "t", {"a": 1})
    assert base != audit_chain.row_hash("p", "o2", "control", "t", {"a": 1})
    rows = [{"id": 1, "ts": "t", "entry": {"a": 1}, "prev_hash": "p", "row_hash": base}]
    assert audit_chain.verify(rows, "o", "control")["ok"]
    assert select  # imported for symmetry with the DB edits above
