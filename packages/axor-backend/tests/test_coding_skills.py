"""Skills turned off for the team: the plane keeps the list, every sidecar
fetches it on its ingest key, and the refusals they ship are counted back."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.auth import required_scope


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/c.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def test_a_skill_turned_off_is_in_every_sidecars_policy(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/coding/policy")).json() == {"skills_off": []}
    r = await client.put("/v1/coding/skills/test-first", json={"off": True})
    assert r.status_code == 200 and r.json() == {"skills_off": ["test-first"]}
    await client.put("/v1/coding/skills/house-style", json={"off": True})
    assert (await client.get("/v1/coding/policy")).json() == {
        "skills_off": ["house-style", "test-first"]}
    await client.put("/v1/coding/skills/house-style", json={"off": False})
    listed = (await client.get("/v1/coding/skills")).json()
    assert [x["skill"] for x in listed["off"]] == ["test-first"] and listed["off"][0]["ts"]
    assert (await client.put("/v1/coding/skills/a b", json={"off": True})).status_code in (400, 404)
    assert (await client.put("/v1/coding/skills/x", json={"off": "yes"})).status_code == 400


async def test_refusals_the_sidecars_ship_are_counted_per_skill(client: httpx.AsyncClient) -> None:
    reason = ("axor: the skill 'test-first' is turned off from the Control Plane — "
              "carry on without it.")
    # the line shape axor_wrap.plane.client.PlaneClient._line sends
    lines = [{"schema_version": "1.0", "seq": i, "node_id": "claude-code-dev-1",
              "kind": "tool_call", "ts": "2026-11-16T10:00:00+00:00", "causal_root": None,
              "verdict": "deny", "gate": "capability",
              "payload": {"tool": "Skill", "args": {"skill": "test-first"}, "reason": reason,
                          "tool_call_id": f"t{i}"}} for i in range(2)]
    r = await client.post("/v1/plane/claude-code-dev-1/telemetry",
                          json={"run_id": "claude-code-dev-1-r1", "events": lines})
    assert r.status_code < 300, r.text
    assert (await client.get("/v1/coding/skills")).json()["attempts"] == {"test-first": 2}


def test_the_sidecars_key_can_read_the_policy_and_not_change_it() -> None:
    assert required_scope("GET", "/v1/coding/policy") == "ingest"
    assert required_scope("GET", "/v1/coding/skills") == "read"
    assert required_scope("PUT", "/v1/coding/skills/x") == "operate"
