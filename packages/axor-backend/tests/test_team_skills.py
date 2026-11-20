"""Skills shared for the team: uploaded on a developer's key, turned on by an
operator, and only then named in the policy the sidecars fetch."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app

SKILL = "---\nname: changelog-entries\ndescription: one line per change\n---\nAdd it.\n"


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def _share(c: httpx.AsyncClient, content: str = SKILL) -> dict:
    r = await c.post("/v1/coding/skill-bundles", json={"name": "changelog-entries", "files": [
        {"path": "SKILL.md", "content": content}, {"path": "t/entry.md", "content": "- x\n"}]})
    assert r.status_code == 201, r.text
    return r.json()


async def _policy(c: httpx.AsyncClient) -> dict:
    return (await c.get("/v1/coding/policy")).json()


async def test_shared_reaches_nobody_until_turned_on(client: httpx.AsyncClient) -> None:
    v1 = await _share(client)
    assert v1["live_version"] is None
    assert (await _policy(client))["skills_team"] == []
    assert (await client.get("/v1/coding/skill-bundles/changelog-entries")).status_code == 404
    r = await client.put("/v1/coding/skills/changelog-entries", json={"team": True})
    assert r.json() == {"name": "changelog-entries", "team": True, "version": v1["version"]}
    assert (await _policy(client))["skills_team"] == [
        {"name": "changelog-entries", "version": v1["version"]}]
    bundle = (await client.get("/v1/coding/skill-bundles/changelog-entries")).json()
    assert [f["path"] for f in bundle["files"]] == ["SKILL.md", "t/entry.md"]
    # a new upload does not replace what is on until it is turned on again
    v2 = await _share(client, SKILL + "Also mention the issue.\n")
    assert v2["version"] != v1["version"] and v2["live_version"] == v1["version"]
    assert (await _policy(client))["skills_team"][0]["version"] == v1["version"]
    row = (await client.get("/v1/coding/skills")).json()["team"][0]
    assert row["latest"]["version"] == v2["version"] and row["live"]["version"] == v1["version"]
    await client.put("/v1/coding/skills/changelog-entries", json={"team": True})
    assert (await _policy(client))["skills_team"][0]["version"] == v2["version"]


async def test_turned_off_for_the_team_is_off_the_team(client: httpx.AsyncClient) -> None:
    await _share(client)
    await client.put("/v1/coding/skills/changelog-entries", json={"team": True})
    await client.put("/v1/coding/skills/changelog-entries", json={"off": True})
    policy = await _policy(client)
    assert policy["skills_team"] == [] and policy["skills_off"] == ["changelog-entries"]
    r = await client.put("/v1/coding/skills/changelog-entries", json={"team": True})
    assert r.status_code == 409


@pytest.mark.parametrize("files", [
    [{"path": "../x.md", "content": "x"}, {"path": "SKILL.md", "content": SKILL}],
    [{"path": "/etc/x", "content": "x"}, {"path": "SKILL.md", "content": SKILL}],
    [{"path": ".git/config", "content": "x"}, {"path": "SKILL.md", "content": SKILL}],
    [{"path": "README.md", "content": "no skill file"}],
])
async def test_a_bundle_stays_inside_its_folder(client: httpx.AsyncClient, files: list) -> None:
    r = await client.post("/v1/coding/skill-bundles", json={"name": "s", "files": files})
    assert r.status_code == 400


async def test_turning_on_needs_a_shared_skill(client: httpx.AsyncClient) -> None:
    r = await client.put("/v1/coding/skills/never-shared", json={"team": True})
    assert r.status_code == 404


async def test_example_shared_skill(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/demo/seed-team-skill")).status_code == 200
    await client.post("/v1/demo/seed-team-skill")
    team = (await client.get("/v1/coding/skills")).json()["team"]
    assert [t["name"] for t in team] == ["changelog-entries"] and team[0]["live"] is None
