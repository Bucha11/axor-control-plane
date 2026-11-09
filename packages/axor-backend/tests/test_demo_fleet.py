"""The example fleet: Live agents has something real to show without an adapter.

Before this, two of its three surfaces were empty on every deployment without
a node running axor-sentinel and axor-probe — the demo spawn posts neither. The
seed builds both with those libraries and posts them through the plane's own
ingest routes, so these tests read the result back through the same routes the
UI does.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend import demo
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={}, allow_unsigned=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        yield c


async def _seed(client: httpx.AsyncClient) -> dict:
    r = await client.post("/v1/demo/seed-fleet")
    assert r.status_code == 200, r.text
    return r.json()


async def test_the_fleet_is_on_the_map_and_listed(client: httpx.AsyncClient) -> None:
    await _seed(client)
    topo = (await client.get("/v1/plane/topology")).json()
    ids = {n["node_id"] for n in topo["nodes"]}
    assert {demo.TREE_ORCH, demo.TREE_RESEARCH, demo.TREE_SCRAPER, demo.TREE_WRITER} <= ids
    listed = {n["node_id"] for n in (await client.get("/v1/plane/nodes")).json()}
    assert demo.FLEET_PROBE_NODE in listed


async def test_sentinel_flags_what_its_own_predicates_flag(client: httpx.AsyncClient) -> None:
    await _seed(client)
    url = f"/v1/plane/{demo.FLEET_SENTINEL_NODE}/reputation"
    levels = (await client.get(url)).json()["reputation"]["resource_level"]
    assert levels["crm:customers"] == "FLAGGED"
    assert levels["s3:weekly-exports"] == "WATCH"
    assert levels["cache:rates"] == "CLEAN"


async def test_probe_reports_drift_with_a_repair_proposal(client: httpx.AsyncClient) -> None:
    await _seed(client)
    report = (await client.get(f"/v1/plane/{demo.FLEET_PROBE_NODE}/probe-report")).json()
    assert report["latest"]["overall_verdict"] == "DRIFT_DETECTED"
    repair = (await client.get(f"/v1/plane/{demo.FLEET_PROBE_NODE}/repair")).json()
    assert repair["proposal"]["excision"] == ["frag_web_3"]


async def test_reseeding_does_not_pile_on(client: httpx.AsyncClient) -> None:
    await _seed(client)
    second = await _seed(client)
    assert second["seeded"] == ["ex_tree"]
    hist = (await client.get(f"/v1/plane/{demo.FLEET_PROBE_NODE}/probe-report")).json()["history"]
    assert len(hist) == 1


async def test_a_reprobe_without_a_cut_is_refused(client: httpx.AsyncClient) -> None:
    await _seed(client)
    r = await client.post("/v1/demo/fleet-reprobe")
    assert r.status_code == 409


async def test_cut_then_reprobe_closes_the_heal(client: httpx.AsyncClient) -> None:
    await _seed(client)
    node = demo.FLEET_PROBE_NODE
    shaped = await client.post(f"/v1/plane/{node}/repair/excision-request",
                               json={"reason": "web result #3 carries an injected instruction"})
    assert shaped.status_code == 200, shaped.text
    cmd = await client.post(f"/v1/plane/{node}/command", json=shaped.json())
    assert cmd.status_code == 202, cmd.text
    r = await client.post("/v1/demo/fleet-reprobe")
    assert r.status_code == 201, r.text
    outcome = r.json()["heals_verified"]
    assert outcome and outcome[0]["resolved"] is True
    repair = (await client.get(f"/v1/plane/{node}/repair")).json()
    assert repair["pending"] is None
