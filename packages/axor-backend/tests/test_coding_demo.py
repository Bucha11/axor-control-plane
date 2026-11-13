"""Example coding agents: two sessions on one ledger, and a tree run."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/c.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def test_the_example_shows_the_shared_ledger_a_tree_and_the_seat_meter(
        client: httpx.AsyncClient) -> None:
    for _ in range(2):  # re-seeding is safe
        r = await client.post("/v1/demo/seed-coding")
        assert r.status_code == 200, r.text
    codex = (await client.get("/v1/plane/codex-example-77b0e4d2/runs")).json()["runs"]
    assert codex and codex[0]["denied"] == 1
    tree = (await client.get("/v1/trees/tree-example-coding")).json()
    assert tree["status"] == "running" and len(tree["nodes"]["read"]["runs"]) == 5
    month = (await client.get("/v1/license/usage?months=1")).json()["months"][0]
    assert month["active_developers"] == 2 and month["peak_nodes"] == 0
