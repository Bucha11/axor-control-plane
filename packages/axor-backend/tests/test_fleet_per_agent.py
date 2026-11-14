"""A multi-agent run is recorded once, under the node that started it; each
agent in it is counted for the work it did — runs, calls, denials — and the
fleet's total still counts the run once."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/f.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def test_each_agent_in_a_tree_run_counts_its_own_work(client) -> None:  # noqa: ANN001
    assert (await client.post("/v1/demo/seed-fleet")).status_code == 200
    fleet = (await client.get("/v1/plane/fleet")).json()
    rows = {r["node_id"]: r for r in fleet["nodes"]}
    for node in ("tree-orch", "tree-research", "tree-scraper", "tree-writer"):
        assert rows[node]["runs"] == 1, node
        assert rows[node]["last_ts"], node
    assert rows["tree-scraper"]["calls"] == 1 and rows["tree-scraper"]["tainted"] == 1
    assert rows["tree-writer"]["denied"] == 1          # its message to an undeclared peer
    assert rows["tree-orch"]["lies"] == 1 and rows["tree-research"]["lies"] == 0
    assert fleet["totals"]["runs"] == 1                # one run, four agents in it
    assert fleet["totals"]["active"] == 0              # the run answered: nobody is running
    runs = (await client.get("/v1/plane/tree-research/runs")).json()["runs"]
    assert [r["run_id"] for r in runs] == ["ex_tree"]
