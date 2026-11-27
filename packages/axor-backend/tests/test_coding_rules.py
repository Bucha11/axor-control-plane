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


async def test_a_forum_or_pastebin_is_never_a_trusted_source(
        client: httpx.AsyncClient, priv, monkeypatch) -> None:  # noqa: ANN001, F811
    """QA-100: pastebin.com and stackoverflow.com were trusted, though the
    screen says never a forum or a pastebin. One saved before stays."""
    owner = _bearer(_mint(priv, role="owner"))
    for host in ("pastebin.com", "stackoverflow.com", "*.stackexchange.com", "www.reddit.com",
                 "superuser.com"):
        r = await client.put("/v1/coding/exceptions", headers=owner,
                             json={"trusted_sources": ["pypi.org", host]})
        assert r.status_code == 400, host
        assert "a forum or a pastebin is never a trusted source" in r.json()["detail"]
    monkeypatch.setattr("axor_backend.routers.coding_api._OPEN_HOSTS", ())
    r = await client.put("/v1/coding/exceptions", headers=owner,
                         json={"trusted_sources": ["pastebin.com"]})
    assert r.status_code == 200, r.text
    monkeypatch.undo()
    r = await client.put("/v1/coding/exceptions", headers=owner,
                         json={"trusted_sources": ["pastebin.com", "pypi.org"]})
    assert r.status_code == 200, r.text


async def test_a_code_host_already_trusted_does_not_block_the_list(
        client: httpx.AsyncClient, priv, monkeypatch) -> None:  # noqa: ANN001, F811
    """A team that trusted github.com before it was refused still edits its
    list: the saved host stays until they remove it; a new one is refused."""
    owner = _bearer(_mint(priv, role="owner"))
    monkeypatch.setattr("axor_backend.routers.coding_api._CODE_HOSTS", ())
    r = await client.put("/v1/coding/exceptions", headers=owner,
                         json={"trusted_sources": ["github.com"]})
    assert r.status_code == 200, r.text
    monkeypatch.undo()
    r = await client.put("/v1/coding/exceptions", headers=owner,
                         json={"trusted_sources": ["github.com", "pypi.org"]})
    assert r.status_code == 200, r.text
    r = await client.put("/v1/coding/exceptions", headers=owner,
                         json={"trusted_sources": ["github.com", "gitlab.com"]})
    assert r.status_code == 400 and "gitlab.com" in r.json()["detail"]


async def test_a_public_suffix_or_a_blog_platform_is_never_a_trusted_source(
        client: httpx.AsyncClient, priv, monkeypatch) -> None:  # noqa: ANN001, F811
    """QA-210, QA-211: *.co.uk, *.com.au and *.github.io trusted every site
    anyone registers under them; medium.com and dev.to anyone publishes on.
    One site under a suffix is the team's to name; one saved before stays."""
    owner = _bearer(_mint(priv, role="owner"))
    for host in ("*.co.uk", "*.com.au", "co.uk", "*.co.za", "*.github.io", "*.blogspot.com",
                 "*.wordpress.com", "*.substack.com", "*.vercel.app", "*.netlify.app",
                 "*.pages.dev", "*.herokuapp.com", "github.io"):
        r = await client.put("/v1/coding/exceptions", headers=owner,
                             json={"trusted_sources": ["pypi.org", host]})
        assert r.status_code == 400, host
        assert "never a trusted source" in r.json()["detail"] and host in r.json()["detail"]
    for host in ("medium.com", "dev.to", "*.medium.com", "someone.medium.com"):
        r = await client.put("/v1/coding/exceptions", headers=owner,
                             json={"trusted_sources": ["pypi.org", host]})
        assert r.status_code == 400, host
        assert "a blog platform is never a trusted source" in r.json()["detail"]
    for host in ("docs.example.co.uk", "*.example.com.au", "pallets.github.io",
                 "*.readthedocs.io"):
        r = await client.put("/v1/coding/exceptions", headers=owner,
                             json={"trusted_sources": ["pypi.org", host]})
        assert r.status_code == 200, (host, r.text)
    # saved before the refusal: kept while the team edits the rest of its list
    monkeypatch.setattr("axor_backend.routers.coding_api._PUBLIC_SUFFIXES", frozenset())
    monkeypatch.setattr("axor_backend.routers.coding_api._BLOG_HOSTS", ())
    r = await client.put("/v1/coding/exceptions", headers=owner,
                         json={"trusted_sources": ["*.github.io", "dev.to"]})
    assert r.status_code == 200, r.text
    monkeypatch.undo()
    r = await client.put("/v1/coding/exceptions", headers=owner,
                         json={"trusted_sources": ["*.github.io", "dev.to", "pypi.org"]})
    assert r.status_code == 200, r.text
