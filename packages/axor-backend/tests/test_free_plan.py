"""The free plan on the plane: one developer and one agent family for coding
agents. Past that a session is kept but locked — named in /entitlements and
refused an operator command (402) — and a tree mixing agent families likewise.
A paid rung locks nothing. The demo's example sessions never count."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.ee.license import License
from axor_backend.tenancy import PUBLIC_ORG

TOKEN = "master-secret-xyz"


@pytest.fixture
async def app_client(tmp_path: pathlib.Path) -> AsyncIterator[tuple]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/d.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test",
                                 headers={"Authorization": f"Bearer {TOKEN}"}) as c, \
            app.router.lifespan_context(app):
        yield app, c


async def _session(c: httpx.AsyncClient, node: str, agent: str, dev: str) -> None:
    r = await c.post(f"/v1/plane/{node}/telemetry", json={
        "run_id": f"{node}-hb", "scenario": "live",
        "events": [{"seq": 0, "kind": "heartbeat",
                    "payload": {"applied_version": 0, "level": "NORMAL",
                                "budget_remaining": None}}]})
    assert r.status_code == 202, r.text
    r = await c.post(f"/v1/plane/{node}/profile",
                     json={"kind": "coding", "agent": agent, "developer": dev})
    assert r.status_code == 204, r.text


async def _pause(c: httpx.AsyncClient, node: str) -> httpx.Response:
    return await c.post(f"/v1/plane/{node}/command",
                        json={"version": 1, "operator": "op", "state": {"paused": True}})


async def test_free_plan_keeps_one_developer_and_one_agent_family(app_client) -> None:  # noqa: ANN001
    _, c = app_client
    await _session(c, "claude-code-a1", "claude-code", "dev-ada")
    await _session(c, "claude-code-a2", "claude-code", "dev-ada")
    await _session(c, "codex-a3", "codex", "dev-ada")          # second family
    await _session(c, "claude-code-b1", "claude-code", "dev-bob")  # second developer

    ent = (await c.get("/v1/license/entitlements")).json()
    assert ent["free"] and ent["developers"] == 1 and ent["agent_families"] == 1
    assert ent["agent_family"] == "claude-code"
    assert ent["locked"] == {"codex-a3": ["agent_family"], "claude-code-b1": ["developer"]}

    assert (await _pause(c, "claude-code-a2")).status_code == 202
    r = await _pause(c, "codex-a3")
    assert r.status_code == 402
    assert r.json()["detail"]["error"] == "plan_required"
    assert "mixing Claude Code and Codex" in r.json()["detail"]["detail"]


async def test_a_tree_mixing_agent_families_is_locked_on_the_free_plan(app_client) -> None:  # noqa: ANN001
    _, c = app_client
    doc = {"task": "t", "nodes": {"impl": {"agent": "claude-code", "runs": []},
                                  "review": {"agent": "codex", "runs": []}}}
    assert (await c.post("/v1/trees/tree-mix1", json=doc)).status_code == 204
    one = {"task": "t", "nodes": {"impl": {"agent": "claude-code", "runs": []},
                                  "review": {"agent": "claude-code", "runs": []}}}
    assert (await c.post("/v1/trees/tree-one1", json=one)).status_code == 204
    ent = (await c.get("/v1/license/entitlements")).json()
    assert ent["locked_trees"] == {"tree-mix1": ["agent_family"]}
    assert (await _pause(c, "tree-mix1")).status_code == 402


async def test_a_paid_workspace_locks_nothing(app_client) -> None:  # noqa: ANN001
    app, c = app_client
    app.state.licenses[PUBLIC_ORG] = License(
        organization="acme", workspace_tier="coding", governed_node_ceiling=1,
        expires_at="2999-01-01")
    await _session(c, "claude-code-a1", "claude-code", "dev-ada")
    await _session(c, "codex-b1", "codex", "dev-bob")
    ent = (await c.get("/v1/license/entitlements")).json()
    assert not ent["free"] and ent["locked"] == {} and ent["agent_families"] is None
    assert (await _pause(c, "codex-b1")).status_code == 202


async def test_the_example_sessions_do_not_use_up_the_free_plan(app_client) -> None:  # noqa: ANN001
    _, c = app_client
    assert (await c.post("/v1/demo/seed-coding")).status_code in (200, 201)
    await _session(c, "codex-mine", "codex", "dev-me")
    ent = (await c.get("/v1/license/entitlements")).json()
    assert ent["locked"] == {} and ent["locked_trees"] == {}
    assert ent["agent_family"] == "codex"


async def test_a_free_workspace_can_switch_the_family_it_keeps(app_client) -> None:  # noqa: ANN001
    _, c = app_client
    await _session(c, "codex-first", "codex", "dev-ada")
    await _session(c, "claude-code-later", "claude-code", "dev-ada")
    ent = (await c.get("/v1/license/entitlements")).json()
    assert ent["agent_family"] == "codex" and not ent["agent_family_chosen"]
    assert ent["locked"] == {"claude-code-later": ["agent_family"]}

    r = await c.put("/v1/license/agent-family", json={"agent": "claude-code"})
    assert r.status_code == 200
    ent = (await c.get("/v1/license/entitlements")).json()
    assert ent["agent_family"] == "claude-code" and ent["agent_family_chosen"]
    assert ent["locked"] == {"codex-first": ["agent_family"]}
    assert (await _pause(c, "claude-code-later")).status_code == 202

    bad = await c.put("/v1/license/agent-family", json={"agent": "notepad"})
    assert bad.status_code == 400
