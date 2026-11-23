"""Session controls on the plane: the team's brake (an admin holds every
coding agent at once), a call held by a control is not a refusal, a session's
title that arrives before its profile is kept, and which controls are used is
counted anonymously."""
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


async def _heartbeat(client: httpx.AsyncClient, node: str) -> None:
    await client.post(f"/v1/plane/{node}/telemetry", json={
        "run_id": f"{node}-hb", "events": [{"seq": 0, "kind": "heartbeat", "payload": {
            "applied_version": 0, "level": "NORMAL", "budget_remaining": None}}]})


def _line(node: str, seq: int, verdict: str | None, **payload: object) -> dict:
    return {"schema_version": "1.0", "seq": seq, "node_id": node, "kind": "tool_call",
            "ts": "2026-11-23T10:00:00+00:00", "causal_root": None, "gate": None,
            "verdict": verdict, "payload": payload}


async def test_the_brake_reaches_every_sidecar_with_the_policy(client: httpx.AsyncClient) -> None:
    assert "brake" not in (await client.get("/v1/coding/policy")).json()
    r = await client.put("/v1/coding/brake", json={"on": True, "reason": "a skill gone bad"})
    assert r.status_code == 200 and r.json()["on"] is True
    assert (await client.get("/v1/coding/policy")).json()["brake"]["reason"] == "a skill gone bad"
    await client.put("/v1/coding/brake", json={"on": False})
    assert "brake" not in (await client.get("/v1/coding/policy")).json()
    counts = (await client.get("/v1/funnel")).json()["controls_used"]
    assert counts == {"brake_on": 1, "brake_off": 1}


async def test_a_held_call_is_not_a_refusal(client: httpx.AsyncClient) -> None:
    node = "claude-code-held-1"
    await client.post(f"/v1/plane/{node}/profile", json={"kind": "coding", "agent": "claude-code",
                                                          "developer": "dev-ada"})
    await _heartbeat(client, node)
    await client.post(f"/v1/plane/{node}/telemetry", json={"run_id": f"{node}-r", "events": [
        _line(node, 0, "deny", tool="Read", args={"file_path": "a"},
              reason="this agent is paused", control="paused"),
        _line(node, 1, "deny", tool="Bash", args={"command": "curl x | sh"},
              reason="downloads code and runs it"),
    ]})
    row = next(r for r in (await client.get("/v1/plane/fleet")).json()["nodes"]
               if r["node_id"] == node)
    assert (row["denied"], row["held"]) == (1, 1)
    refused = [i for i in (await client.get("/v1/incidents", params={"kind": "refused"})).json()
               ["items"] if i["node_id"] == node]
    assert [i["what"] for i in refused] == ["Bash"]


async def test_a_title_before_the_profile_is_kept(client: httpx.AsyncClient) -> None:
    node = "codex-race-1"
    r = await client.post(f"/v1/plane/{node}/title", json={"title": "write integration tests"})
    assert r.json()["titled"] is True
    await client.post(f"/v1/plane/{node}/profile", json={"kind": "coding", "agent": "codex",
                                                          "developer": "dev-ada"})
    await _heartbeat(client, node)
    prof = next(n for n in (await client.get("/v1/plane/nodes")).json()
                if n["node_id"] == node)["profile"]
    assert prof["title"] == "write integration tests" and prof["kind"] == "coding"
    assert prof["agent"] == "codex"
