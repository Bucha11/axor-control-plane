"""M2: topology derived from traced events; signed root-command cascade stop.

Spec v2 Ch.4 §6 — tree shape comes from node_spawned/message events, never
from a node self-reporting its parent; the plane commands the subtree ROOT
and the tree distributes the stop along spawn edges.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.signing import signed_payload
from nacl.signing import SigningKey

OP = "op_dmitrii"


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey(b"\x02" * 32)


def _app(tmp_path: pathlib.Path, keys: dict, allow_unsigned: bool):
    return create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys=keys,
        allow_unsigned=allow_unsigned,
    )


async def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    )


@pytest.fixture
async def open_client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = _app(tmp_path, {}, allow_unsigned=True)
    async with await _client(app) as c:
        async with app.router.lifespan_context(app):
            yield c


@pytest.fixture
async def signed_client(
    tmp_path: pathlib.Path, signing_key: SigningKey
) -> httpx.AsyncClient:
    app = _app(
        tmp_path, {OP: signing_key.verify_key.encode().hex()}, allow_unsigned=False
    )
    async with await _client(app) as c:
        async with app.router.lifespan_context(app):
            yield c


async def test_topology_derives_from_tree_run(open_client: httpx.AsyncClient) -> None:
    await open_client.post("/v1/demo/seed-tree-run")
    topo = (await open_client.get("/v1/plane/topology")).json()

    ids = {n["node_id"] for n in topo["nodes"]}
    assert {"tree-orch", "tree-research", "tree-writer", "tree-scraper"} <= ids

    edges = {(e["from"], e["to"], e["kind"]) for e in topo["edges"]}
    # delegation edges from traced node_spawned — not from a parent field
    assert ("tree-orch", "tree-research", "delegation") in edges
    assert ("tree-orch", "tree-writer", "delegation") in edges
    assert ("tree-research", "tree-scraper", "delegation") in edges
    # the lateral edge from message events
    assert ("tree-research", "tree-writer", "lateral") in edges
    # the undeclared peer target renders as an opaque peer node
    peer = next(n for n in topo["nodes"] if n["node_id"] == "partner-agent")
    assert peer["kind"] == "peer"
    peer_edge = next(e for e in topo["edges"] if e["kind"] == "peer")
    assert peer_edge["denied"] == 1 and peer_edge["last_gate"] == "message_gate"
    assert all(n["kind"] == "self" for n in topo["nodes"]
               if n["node_id"] != "partner-agent")


async def test_topology_counts_messages_per_edge(open_client: httpx.AsyncClient) -> None:
    await open_client.post("/v1/demo/seed-tree-run")
    topo = (await open_client.get("/v1/plane/topology")).json()
    lateral = next(e for e in topo["edges"] if e["kind"] == "lateral")
    assert lateral["messages"] == 1 and lateral["denied"] == 0


async def test_topology_peer_target_is_opaque(open_client: httpx.AsyncClient) -> None:
    """A node only ever seen as a peer-edge target renders as an opaque peer:
    no desired/reported posture attached (structurally absent, not greyed)."""
    lines = [{
        "schema_version": "1.0", "seq": 0, "node_id": "our-writer",
        "kind": "message_sent", "ts": "t", "causal_root": None, "gate": None,
        "verdict": "pass",
        "payload": {"to": "partner-agent", "edge_kind": "peer", "msg_id": "m",
                    "value_ref": "v", "carried": {"root": {"sources": [],
                                                           "sensitive": False}}},
    }]
    r = await open_client.post("/v1/plane/our-writer/telemetry",
                               json={"run_id": "r_peer", "events": lines})
    assert r.status_code in (200, 202)
    topo = (await open_client.get("/v1/plane/topology")).json()
    peer = next(n for n in topo["nodes"] if n["node_id"] == "partner-agent")
    assert peer["kind"] == "peer"
    assert "desired" not in peer and "reported" not in peer


async def test_cascade_stop_unsigned_keeps_bfs_fallback(
    open_client: httpx.AsyncClient,
) -> None:
    # legacy parent-field topology
    for nid, state in (("root-n", {"paused": False}),
                       ("child-n", {"parent": "root-n"})):
        v = 1
        await open_client.post(f"/v1/plane/{nid}/command",
                               json={"version": v, "state": state})
    r = (await open_client.post("/v1/plane/root-n/cascade-stop")).json()
    assert r["mode"] == "bfs_fallback"
    assert set(r["stopped"]) == {"root-n", "child-n"}


async def test_cascade_stop_signed_is_one_root_command(
    signed_client: httpx.AsyncClient, signing_key: SigningKey
) -> None:
    """With operator keys set, cascade stop is a SINGLE signed command to the
    subtree root — the tree distributes it (spec v2 Ch.4 §6). The 409 that
    used to block signed cascade is gone."""
    ts = datetime.now(UTC).isoformat()
    delta = {"stopped": True, "cascade": True}
    sig = signing_key.sign(
        signed_payload("tree-orch", 1, delta, ts)
    ).signature.hex()
    r = await signed_client.post(
        "/v1/plane/tree-orch/cascade-stop",
        json={"version": 1, "operator": OP, "timestamp": ts, "sig": sig},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["mode"] == "root_command" and body["stopped"] == ["tree-orch"]

    desired = (await signed_client.get("/v1/plane/nodes")).json()
    orch = next(n for n in desired if n["node_id"] == "tree-orch")
    assert orch["desired"]["state"]["stopped"] is True
    assert orch["desired"]["state"]["cascade"] is True


async def test_cascade_stop_signed_rejects_bad_signature(
    signed_client: httpx.AsyncClient,
) -> None:
    r = await signed_client.post(
        "/v1/plane/tree-orch/cascade-stop",
        json={"version": 1, "operator": OP, "timestamp": "t", "sig": "00" * 64},
    )
    assert r.status_code == 403


async def test_per_line_node_id_wins_over_batch(open_client: httpx.AsyncClient) -> None:
    """Multi-node runs: each line's own node_id is stored (spec v2 Ch.4 —
    a tree of N nodes streams N identities through one run)."""
    lines = [
        {"schema_version": "1.0", "seq": 0, "node_id": "n-a", "kind": "node_spawned",
         "ts": "t", "causal_root": None, "gate": None, "verdict": None,
         "payload": {"child_id": "n-b", "parent_id": "n-a", "depth": 1,
                     "edge_kind": "delegation"}},
    ]
    await open_client.post("/v1/plane/other-node/telemetry",
                           json={"run_id": "r_multi", "events": lines})
    topo = (await open_client.get("/v1/plane/topology")).json()
    edges = {(e["from"], e["to"], e["kind"]) for e in topo["edges"]}
    assert ("n-a", "n-b", "delegation") in edges
