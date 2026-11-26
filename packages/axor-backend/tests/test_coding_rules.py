"""The team's rules for coding agents: decided once by an admin, fetched by
every sidecar with the skills policy. Until an admin saves them, the sidecars
keep their own defaults (and each repository's .axor/rules.json)."""
from __future__ import annotations

import httpx
from test_identity_auth import _bearer, _mint, client, priv  # noqa: F401


async def test_the_admin_decides_and_every_sidecar_fetches_it(
        client: httpx.AsyncClient, priv) -> None:  # noqa: ANN001, F811
    owner = _bearer(_mint(priv, role="owner"))
    member = _bearer(_mint(priv, role="member", sub="usr_dev2", eml="dev2@acme.io"))
    screen = (await client.get("/v1/coding/rules", headers=owner)).json()
    assert screen["set"] is False and screen["mode"] == "enforce"
    ids = {r["id"] for r in screen["catalog"]}
    assert {"secrets", "sends_out", "download_and_run"} <= ids
    # nothing saved: the sidecars keep their own defaults
    assert "rules" not in (await client.get("/v1/coding/policy", headers=member)).json()

    # a member steers sessions, but what every agent may do is the admin's
    r = await client.put("/v1/coding/rules", headers=member, json={"allow": ["secrets"]})
    assert r.status_code == 403
    r = await client.put("/v1/coding/rules", headers=owner,
                         json={"allow": ["destructive"], "mode": "observe"})
    assert r.status_code == 200 and r.json()["by"]
    policy = (await client.get("/v1/coding/policy", headers=member)).json()
    assert policy["rules"]["allow"] == ["destructive"]
    assert policy["rules"]["mode"] == "observe"


async def test_nonsense_and_the_agents_own_guard_are_refused(
        client: httpx.AsyncClient, priv) -> None:  # noqa: ANN001, F811
    owner = _bearer(_mint(priv, role="owner"))
    assert (await client.put("/v1/coding/rules", headers=owner,
                             json={"allow": ["teleport"]})).status_code == 400
    assert (await client.put("/v1/coding/rules", headers=owner,
                             json={"allow": [], "mode": "loose"})).status_code == 400


async def test_exceptions_and_trusted_sources_are_the_teams_and_kept(
        client: httpx.AsyncClient, priv) -> None:  # noqa: ANN001, F811
    owner = _bearer(_mint(priv, role="owner"))
    member = _bearer(_mint(priv, role="member", sub="usr_dev2", eml="dev2@acme.io"))
    r = await client.put("/v1/coding/rules", headers=owner, json={
        "allow": [], "mode": "enforce",
        "exceptions": [{"rule": "secrets", "path": "tests/fixtures/*", "note": "fake credentials"},
                       {"rule": "secrets", "command": "env | grep JWT"}],
        "trusted_sources": ["pypi.org", "*.readthedocs.io"]})
    assert r.status_code == 200, r.text
    assert r.json()["exceptions"][0]["by"]
    # saving the rules alone keeps them
    await client.put("/v1/coding/rules", headers=owner, json={"allow": [], "mode": "observe"})
    rules = (await client.get("/v1/coding/policy", headers=member)).json()["rules"]
    assert [e.get("path") or e.get("command") for e in rules["exceptions"]] == \
        ["tests/fixtures/*", "env | grep JWT"]
    assert rules["trusted_sources"] == ["*.readthedocs.io", "pypi.org"]


async def test_an_exception_is_one_thing_never_everything(
        client: httpx.AsyncClient, priv) -> None:  # noqa: ANN001, F811
    owner = _bearer(_mint(priv, role="owner"))
    for bad in ({"rule": "secrets", "path": "*"}, {"rule": "agent_config", "path": ".claude/x"},
                {"rule": "secrets"}):
        r = await client.put("/v1/coding/rules", headers=owner,
                             json={"allow": [], "exceptions": [bad]})
        assert r.status_code == 400, bad
    r = await client.put("/v1/coding/rules", headers=owner,
                         json={"allow": [], "trusted_sources": ["com"]})
    assert r.status_code == 400


async def test_a_too_long_exception_says_so(
        client: httpx.AsyncClient, priv) -> None:  # noqa: ANN001, F811
    """QA-025: a 5000-character path was refused as "not everything (*)"."""
    owner = _bearer(_mint(priv, role="owner"))
    r = await client.put("/v1/coding/exceptions", headers=owner,
                         json={"exceptions": [{"rule": "secrets", "path": "a" * 5000}]})
    assert r.status_code == 400
    assert r.json()["detail"] == "an exception is at most 300 characters (this one has 5000)"


async def test_a_code_host_is_never_a_trusted_source(
        client: httpx.AsyncClient, priv) -> None:  # noqa: ANN001, F811
    """QA-029: github.com was trusted silently, though the screen says never
    to trust a site anyone can write to."""
    owner = _bearer(_mint(priv, role="owner"))
    for host in ("github.com", "gist.github.com", "raw.githubusercontent.com", "*.github.com",
                 "gitlab.com", "bitbucket.org"):
        r = await client.put("/v1/coding/exceptions", headers=owner,
                             json={"trusted_sources": ["pypi.org", host]})
        assert r.status_code == 400, host
        assert "a code host is never a trusted source" in r.json()["detail"]
    r = await client.put("/v1/coding/exceptions", headers=owner,
                         json={"trusted_sources": ["pypi.org", "notgithub.com"]})
    assert r.status_code == 200, r.text
