"""The deepest integration: the proxy spawns a REAL axor-core governed node
(IntentLoop), uploads its adapter-fidelity trace to the backend, and keeps a
PlaneClient heartbeating — so the node shows up live on the plane and an operator
cascade-stop reaches it. Proxy + axor-core + backend, all real.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest

pytestmark = pytest.mark.e2e


async def _nodes(http: httpx.AsyncClient) -> list[dict]:
    return (await http.get("/v1/plane/nodes")).json()


async def test_spawned_governed_node_goes_live_and_uploads_its_run(
    proxy_http: httpx.AsyncClient, http: httpx.AsyncClient,
) -> None:
    spawn = (await proxy_http.post("/axor/governed/spawn")).json()
    node_id, run_id = spawn["node_id"], spawn["run_id"]
    assert spawn["events"] >= 1
    # The governed session enforces per-value taint, so at least one egress denial.
    assert spawn["denials"] >= 1

    # Its adapter-fidelity trace was uploaded and is replayable.
    scrub = (await http.get(f"/v1/replay/{run_id}")).json()
    assert len(scrub["steps"]) >= 1

    # The PlaneClient heartbeats — the node appears live within a few seconds.
    for _ in range(40):
        if any(n["node_id"] == node_id for n in await _nodes(http)):
            break
        await asyncio.sleep(0.5)
    else:
        pytest.fail(f"governed node {node_id} never became live on the plane")

    # An operator cascade-stop reaches the live node.
    out = (await http.post(f"/v1/plane/{node_id}/cascade-stop")).json()
    assert node_id in out["stopped"]
