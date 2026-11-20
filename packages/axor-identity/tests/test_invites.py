"""Invitations: an admin makes a single-use link for a role; whoever opens it
signs up (or in) and joins the organization — without having had an account
before. Bound to one address when named, revocable, seven days."""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_identity.app import create_app
from axor_identity.keys import generate_pem, load_signing_key

PW = "correct horse battery"


@pytest.fixture
async def client(tmp_path: pathlib.Path):  # noqa: ANN201
    app = create_app(f"sqlite+aiosqlite:///{tmp_path}/id.db",
                     signing_key=load_signing_key(generate_pem(), kid="k"), signup="closed")
    async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://identity.io") as c:
        yield c


def _h(session: dict) -> dict:
    return {"Authorization": f"Bearer {session['access_token']}"}


async def _owner(app_client: httpx.AsyncClient) -> dict:
    # a closed install still has its first account, made out of band — here
    # through first-only's sibling path: the app's own store
    store = app_client._transport.app.state.store  # type: ignore[attr-defined]
    from axor_identity.passwords import hash_password

    await store.create_user("usr_owner", "ada@acme.io", hash_password(PW), "2026-01-01T00:00:00Z")
    await store.create_org("org_acme", "Acme", "coding", "2026-01-01T00:00:00Z")
    await store.add_membership("usr_owner", "org_acme", "owner", "2026-01-01T00:00:00Z")
    r = await app_client.post("/v1/login", json={"email": "ada@acme.io", "password": PW})
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_teammate_with_no_account_joins_through_the_link(client: httpx.AsyncClient) -> None:
    owner = await _owner(client)
    r = await client.post("/v1/orgs/org_acme/invites", json={"role": "member"}, headers=_h(owner))
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    info = (await client.get(f"/v1/invites/{token}")).json()
    assert info == {"org_name": "Acme", "role": "member", "for_email": None,
                    "expires_ts": r.json()["expires_ts"]}
    # signup is closed on this install, and the invited still get in
    s = await client.post("/v1/signup", json={"email": "sam@acme.io", "password": PW,
                                              "invite": token})
    assert s.status_code == 201, s.text
    assert s.json()["org"] == {"org_id": "org_acme", "role": "member", "tier": "coding"}
    members = (await client.get("/v1/orgs/org_acme/members", headers=_h(owner))).json()["members"]
    assert [(m["email"], m["role"]) for m in members] == [("ada@acme.io", "owner"),
                                                          ("sam@acme.io", "member")]
    # single use
    assert (await client.get(f"/v1/invites/{token}")).status_code == 404
    again = await client.post("/v1/signup", json={"email": "eve@x.io", "password": PW,
                                                  "invite": token})
    assert again.status_code == 404
    # and closed signup still refuses a new workspace
    assert (await client.post("/v1/signup", json={"email": "eve@x.io", "password": PW,
                                                  "org_name": "Eve"})).status_code == 403


async def test_an_account_one_has_accepts_and_gets_a_session_there(
        client: httpx.AsyncClient) -> None:
    owner = await _owner(client)
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    from axor_identity.passwords import hash_password

    await store.create_user("usr_bob", "bob@else.io", hash_password(PW), "2026-01-02T00:00:00Z")
    await store.create_org("org_bob", "Bob's", "community", "2026-01-02T00:00:00Z")
    await store.add_membership("usr_bob", "org_bob", "owner", "2026-01-02T00:00:00Z")
    bob = (await client.post("/v1/login", json={"email": "bob@else.io", "password": PW})).json()
    token = (await client.post("/v1/orgs/org_acme/invites", json={"role": "viewer"},
                               headers=_h(owner))).json()["token"]
    r = await client.post(f"/v1/invites/{token}/accept", headers=_h(bob))
    assert r.status_code == 200 and r.json()["org"]["org_id"] == "org_acme"
    assert r.json()["org"]["role"] == "viewer"


async def test_bound_to_an_address_revocable_and_admin_only(client: httpx.AsyncClient) -> None:
    owner = await _owner(client)
    r = await client.post("/v1/orgs/org_acme/invites",
                          json={"role": "admin", "email": "Sam@Acme.io"}, headers=_h(owner))
    token, invite_id = r.json()["token"], r.json()["invite_id"]
    wrong = await client.post("/v1/signup", json={"email": "eve@x.io", "password": PW,
                                                  "invite": token})
    assert wrong.status_code == 403
    listed = (await client.get("/v1/orgs/org_acme/invites", headers=_h(owner))).json()["invites"]
    assert [i["invite_id"] for i in listed] == [invite_id] and "token_hash" not in listed[0]
    assert (await client.delete(f"/v1/orgs/org_acme/invites/{invite_id}",
                                headers=_h(owner))).status_code == 204
    assert (await client.get(f"/v1/invites/{token}")).status_code == 404
    # a member who is not an admin cannot invite
    t2 = (await client.post("/v1/orgs/org_acme/invites", json={"role": "member"},
                            headers=_h(owner))).json()["token"]
    sam = (await client.post("/v1/signup", json={"email": "sam@acme.io", "password": PW,
                                                 "invite": t2})).json()
    assert (await client.post("/v1/orgs/org_acme/invites", json={"role": "member"},
                              headers=_h(sam))).status_code == 403
    assert (await client.post("/v1/orgs/org_acme/invites", json={"role": "owner"},
                              headers=_h(owner))).status_code == 422
