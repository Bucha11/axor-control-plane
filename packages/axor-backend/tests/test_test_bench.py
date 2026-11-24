"""A production agent says whether it is a test bench; the screens offer to
break its tools only then. The last word wins: the same node restarted for
real users is a test bench no longer."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app

TOKEN = "master-secret-xyz"


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/d.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test",
                                 headers={"Authorization": f"Bearer {TOKEN}"}) as c, \
            app.router.lifespan_context(app):
        yield c


async def _report(client: httpx.AsyncClient, node: str) -> None:
    r = await client.post(f"/v1/plane/{node}/telemetry", json={
        "run_id": f"{node}-hb", "scenario": "live",
        "events": [{"seq": 0, "kind": "heartbeat",
                    "payload": {"applied_version": 0, "level": "NORMAL",
                                "budget_remaining": None}}]})
    assert r.status_code == 202, r.text


async def _profile_of(client: httpx.AsyncClient, node: str) -> dict:
    nodes = (await client.get("/v1/plane/nodes")).json()
    return next(n for n in nodes if n["node_id"] == node)["profile"]


async def test_the_agent_says_it_and_the_last_word_wins(client) -> None:  # noqa: ANN001
    await _report(client, "copilot-staging")
    body = {"kind": "agent", "agent": "axor-wrap", "test_bench": True}
    assert (await client.post("/v1/plane/copilot-staging/profile", json=body)).status_code == 200
    assert (await _profile_of(client, "copilot-staging"))["test_bench"] is True
    body["test_bench"] = False
    await client.post("/v1/plane/copilot-staging/profile", json=body)
    assert "test_bench" not in await _profile_of(client, "copilot-staging")


async def test_only_a_production_agent_is_a_test_bench(client) -> None:  # noqa: ANN001
    await _report(client, "session-1")
    await client.post("/v1/plane/session-1/profile", json={
        "kind": "coding", "agent": "claude-code", "developer": "dev-a", "test_bench": True})
    assert "test_bench" not in await _profile_of(client, "session-1")
    # a truthy string is not the agent's word
    await _report(client, "a2")
    await client.post("/v1/plane/a2/profile", json={"kind": "agent", "test_bench": "yes"})
    assert "test_bench" not in await _profile_of(client, "a2")
