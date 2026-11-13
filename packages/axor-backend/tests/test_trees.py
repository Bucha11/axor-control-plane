"""Agent tree runs: posted as they go, read back per tenant."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app

TOKEN = "master-secret-xyz"


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test",
                                 headers={"Authorization": f"Bearer {TOKEN}"}) as c, \
            app.router.lifespan_context(app):
        yield c


SNAP = {
    "task": "Add rate limiting", "status": "running",
    "stages": [["plan"], ["read"], ["impl"]],
    "nodes": {"plan": {"agent": "claude-code", "model": "claude-fable-5-2", "count": 1,
                       "runs": [{"index": 0, "node_id": "node_a", "status": "ok"}]},
              "read": {"agent": "codex", "model": "luna", "count": 5, "access": "read",
                       "runs": []}},
    "items": [{"ref": "plan#0", "node": "plan", "kind": "plan", "headline": "1. add",
               "untrusted": False, "excised": False}],
    "flow": {}, "excised": [],
}


async def test_a_tree_run_is_posted_replaced_and_listed(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/trees/tree-1a2b", json=SNAP)).status_code == 204
    assert (await client.post("/v1/trees/tree-1a2b",
                              json={**SNAP, "status": "done"})).status_code == 204
    got = (await client.get("/v1/trees/tree-1a2b")).json()
    assert got["status"] == "done" and got["nodes"]["read"]["model"] == "luna"
    listed = (await client.get("/v1/trees")).json()["trees"]
    assert listed == [{"tree_run": "tree-1a2b", "status": "done",
                       "updated_ts": listed[0]["updated_ts"], "task": "Add rate limiting",
                       "nodes": 2, "agents": 1, "members": ["node_a"],
                       "agent_families": ["claude-code", "codex"]}]


async def test_bad_snapshots_are_refused(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/trees/../x", json=SNAP)).status_code in (400, 404)
    weird = await client.post("/v1/trees/t1", json={**SNAP, "status": "weird"})
    assert weird.status_code == 400
    assert (await client.post("/v1/trees/t1", json={**SNAP, "items": {}})).status_code == 400
    assert (await client.get("/v1/trees/nope")).status_code == 404
