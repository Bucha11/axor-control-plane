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
    assert listed == [{"tree_run": "tree-1a2b", "status": "done", "quiet": False,
                       "stages": [
                           [{"node": "plan", "agent": "claude-code", "access": "", "count": 1,
                             "runs": [{"index": 0, "node_id": "node_a", "status": "ok"}]}],
                           [{"node": "read", "agent": "codex", "access": "read", "count": 5,
                             "runs": []}],
                           [{"node": "impl", "agent": "", "access": "", "count": 1, "runs": []}]],
                       "updated_ts": listed[0]["updated_ts"], "task": "Add rate limiting",
                       "nodes": 2, "agents": 1, "members": ["node_a"],
                       "agent_families": ["claude-code", "codex"]}]


async def test_a_late_snapshot_does_not_undo_a_newer_one(client: httpx.AsyncClient) -> None:
    """Snapshots can arrive out of order: an older one (by its seq) is dropped,
    and a finished tree is never put back to running."""
    readers = {**SNAP["nodes"], "read": {**SNAP["nodes"]["read"], "runs": [
        {"index": 0, "node_id": "node_r0", "status": "running"}]}}
    await client.post("/v1/trees/tree-9", json={**SNAP, "nodes": readers, "seq": 3})
    await client.post("/v1/trees/tree-9", json={**SNAP, "seq": 2})
    got = (await client.get("/v1/trees/tree-9")).json()
    assert got["nodes"]["read"]["runs"][0]["node_id"] == "node_r0"
    await client.post("/v1/trees/tree-9", json={**SNAP, "status": "done"})
    await client.post("/v1/trees/tree-9", json={**SNAP, "status": "running"})
    assert (await client.get("/v1/trees/tree-9")).json()["status"] == "done"


async def test_a_tree_gone_quiet_is_said_so(client: httpx.AsyncClient) -> None:
    from axor_backend import storage

    await client.post("/v1/trees/tree-q", json=SNAP)
    assert (await client.get("/v1/trees")).json()["trees"][0]["quiet"] is False
    old = storage.TREE_QUIET_S
    storage.TREE_QUIET_S = -1
    try:
        tree = (await client.get("/v1/trees")).json()["trees"][0]
        assert tree["quiet"] is True and tree["status"] == "running"
        # every run it planned finished: it ended, its last word was lost
        ended = {**SNAP, "nodes": {
            "plan": SNAP["nodes"]["plan"],
            "read": {**SNAP["nodes"]["read"], "count": 1,
                     "runs": [{"index": 0, "node_id": "node_r", "status": "ok"}]}}}
        await client.post("/v1/trees/tree-q", json=ended)
        tree = (await client.get("/v1/trees/tree-q")).json()
        assert tree["quiet"] is False and tree["status"] == "done"
    finally:
        storage.TREE_QUIET_S = old


async def test_bad_snapshots_are_refused(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/trees/../x", json=SNAP)).status_code in (400, 404)
    weird = await client.post("/v1/trees/t1", json={**SNAP, "status": "weird"})
    assert weird.status_code == 400
    assert (await client.post("/v1/trees/t1", json={**SNAP, "items": {}})).status_code == 400
    assert (await client.get("/v1/trees/nope")).status_code == 404
