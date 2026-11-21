"""An admin changes a teammate's role, or takes them off the workspace."""
from __future__ import annotations

import httpx
from test_invites import PW, _h, _owner, client  # noqa: F401


async def _join(c: httpx.AsyncClient, owner: dict, email: str, role: str) -> dict:
    token = (await c.post("/v1/orgs/org_acme/invites", json={"role": role},
                          headers=_h(owner))).json()["token"]
    s = await c.post("/v1/signup", json={"email": email, "password": PW, "invite": token})
    assert s.status_code == 201, s.text
    return s.json()


def _ids(members: list[dict]) -> dict[str, str]:
    return {m["email"]: m["user_id"] for m in members}


async def test_an_admin_changes_a_role_and_it_shows_at_the_next_login(
        client: httpx.AsyncClient) -> None:  # noqa: F811
    owner = await _owner(client)
    await _join(client, owner, "sam@acme.io", "member")
    ids = _ids((await client.get("/v1/orgs/org_acme/members", headers=_h(owner))).json()["members"])
    r = await client.put(f"/v1/orgs/org_acme/members/{ids['sam@acme.io']}", json={"role": "admin"},
                         headers=_h(owner))
    assert r.status_code == 200 and r.json()["role"] == "admin"
    again = (await client.post("/v1/login", json={"email": "sam@acme.io", "password": PW})).json()
    assert again["org"]["role"] == "admin"
    assert (await client.put(f"/v1/orgs/org_acme/members/{ids['sam@acme.io']}",
                             json={"role": "owner"}, headers=_h(owner))).status_code == 422


async def test_what_cannot_be_changed(client: httpx.AsyncClient) -> None:  # noqa: F811
    owner = await _owner(client)
    sam = await _join(client, owner, "sam@acme.io", "member")
    lee = await _join(client, owner, "lee@acme.io", "admin")
    ids = _ids((await client.get("/v1/orgs/org_acme/members", headers=_h(owner))).json()["members"])
    # a member changes nobody's role
    assert (await client.put(f"/v1/orgs/org_acme/members/{ids['lee@acme.io']}",
                             json={"role": "viewer"}, headers=_h(sam))).status_code == 403
    # nobody changes their own, or the owner's
    assert (await client.put(f"/v1/orgs/org_acme/members/{ids['lee@acme.io']}",
                             json={"role": "viewer"}, headers=_h(lee))).status_code == 409
    assert (await client.put(f"/v1/orgs/org_acme/members/{ids['ada@acme.io']}",
                             json={"role": "viewer"}, headers=_h(lee))).status_code == 409
    assert (await client.delete(f"/v1/orgs/org_acme/members/{ids['ada@acme.io']}",
                                headers=_h(lee))).status_code == 409


async def test_an_admin_takes_a_teammate_off(client: httpx.AsyncClient) -> None:  # noqa: F811
    owner = await _owner(client)
    await _join(client, owner, "sam@acme.io", "member")
    ids = _ids((await client.get("/v1/orgs/org_acme/members", headers=_h(owner))).json()["members"])
    r = await client.delete(f"/v1/orgs/org_acme/members/{ids['sam@acme.io']}", headers=_h(owner))
    assert r.status_code == 200
    members = (await client.get("/v1/orgs/org_acme/members", headers=_h(owner))).json()["members"]
    assert [m["email"] for m in members] == ["ada@acme.io"]
    assert (await client.delete(f"/v1/orgs/org_acme/members/{ids['sam@acme.io']}",
                                headers=_h(owner))).status_code == 404
