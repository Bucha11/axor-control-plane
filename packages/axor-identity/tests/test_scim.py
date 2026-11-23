"""SCIM 2.0 provisioning: the workspace's identity provider creates people,
finds them by userName, deactivates them the way Okta and Entra ID do — and a
person deactivated leaves the workspace and their sessions in it end."""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_identity.app import create_app
from axor_identity.keys import generate_pem, load_signing_key

PUBLIC = "https://cp.example.com/identity"
PW = "correct horse battery"
SCIM = "application/scim+json"


@pytest.fixture
async def app(tmp_path: pathlib.Path):  # noqa: ANN201
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/id.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={}, public_url=PUBLIC, mailer=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def c(app):  # noqa: ANN001, ANN201
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://identity") as client:
        yield client


async def _workspace(c: httpx.AsyncClient, app, tier: str = "business") -> tuple[dict, dict]:  # noqa: ANN001
    olga = (await c.post("/v1/signup", json={"email": "olga@acme.dev", "password": PW,
                                             "org_name": "Acme"})).json()
    await app.state.store.mark_verified(olga["user"]["user_id"])
    await app.state.store.set_org_tier(olga["org"]["org_id"], tier, "2999-01-01")
    h = {"Authorization": f"Bearer {olga['access_token']}"}
    made = await c.post(f"/v1/orgs/{olga['org']['org_id']}/scim/token", headers=h)
    return olga, made.json() if made.status_code == 201 else {"status": made.status_code}


def _idp(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": SCIM}


async def test_the_provider_creates_finds_and_deactivates(c, app) -> None:  # noqa: ANN001
    olga, made = await _workspace(c, app)
    org = olga["org"]["org_id"]
    assert made["base_url"] == f"{PUBLIC}/scim/v2" and made["token"].startswith("scim_")
    h = _idp(made["token"])
    r = await c.post("/scim/v2/Users", headers=h, json={
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "userName": "Anna@acme.dev", "active": True,
        "emails": [{"value": "anna@acme.dev", "primary": True}]})
    assert r.status_code == 201 and r.headers["content-type"].startswith(SCIM)
    anna = r.json()
    assert anna["userName"] == "anna@acme.dev" and anna["active"] is True
    # twice is a conflict, as SCIM says
    again = await c.post("/scim/v2/Users", headers=h, json={"userName": "anna@acme.dev"})
    assert again.status_code == 409 and again.json()["scimType"] == "uniqueness"
    found = (await c.get("/scim/v2/Users", headers=h,
                         params={"filter": 'userName eq "anna@acme.dev"'})).json()
    assert found["totalResults"] == 1 and found["Resources"][0]["id"] == anna["id"]
    assert (await c.get(f"/scim/v2/Users/{anna['id']}", headers=h)).status_code == 200
    # she signs in (a password set by reset, say) and holds a session
    members = (await c.get(f"/v1/orgs/{org}/members",
                           headers={"Authorization": f"Bearer {olga['access_token']}"})).json()
    assert {m["email"]: m["role"] for m in members["members"]}["anna@acme.dev"] == "member"
    # Entra ID deactivates: PATCH, "Replace", the value as a string
    r = await c.patch(f"/scim/v2/Users/{anna['id']}", headers=h, json={
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "Replace", "path": "active", "value": "False"}]})
    assert r.status_code == 200 and r.json()["active"] is False
    members = (await c.get(f"/v1/orgs/{org}/members",
                           headers={"Authorization": f"Bearer {olga['access_token']}"})).json()
    assert "anna@acme.dev" not in {m["email"] for m in members["members"]}
    assert (await c.get(f"/scim/v2/Users/{anna['id']}", headers=h)).status_code == 404
    # Okta reactivates: PUT with active true
    r = await c.put(f"/scim/v2/Users/{anna['id']}", headers=h,
                    json={"userName": "anna@acme.dev", "active": True})
    assert r.status_code == 200 and r.json()["active"] is True
    # Okta deactivates: PATCH with a value object
    await c.patch(f"/scim/v2/Users/{anna['id']}", headers=h, json={
        "Operations": [{"op": "replace", "value": {"active": False}}]})
    assert (await c.get(f"/scim/v2/Users/{anna['id']}", headers=h)).status_code == 404
    # and a delete
    await c.put(f"/scim/v2/Users/{anna['id']}", headers=h, json={"active": True})
    assert (await c.delete(f"/scim/v2/Users/{anna['id']}", headers=h)).status_code == 204
    assert (await c.get(f"/scim/v2/Users/{anna['id']}", headers=h)).status_code == 404


async def test_a_deprovisioned_session_cannot_be_refreshed(c, app) -> None:  # noqa: ANN001
    olga, made = await _workspace(c, app)
    org = olga["org"]["org_id"]
    h = _idp(made["token"])
    # an existing account at the domain, already in the workspace
    petr = (await c.post("/v1/signup", json={"email": "petr@acme.dev", "password": PW,
                                             "org_name": "Petr's"})).json()
    await c.post(f"/v1/orgs/{org}/members", json={"email": "petr@acme.dev", "role": "member"},
                 headers={"Authorization": f"Bearer {olga['access_token']}"})
    session = (await c.post("/v1/login", json={"email": "petr@acme.dev", "password": PW,
                                               "org_id": org})).json()
    found = (await c.get("/scim/v2/Users", headers=h,
                         params={"filter": 'userName eq "petr@acme.dev"'})).json()
    await c.delete(f"/scim/v2/Users/{found['Resources'][0]['id']}", headers=h)
    assert (await c.post("/v1/refresh", json={"refresh_token": session["refresh_token"]})
            ).status_code == 401
    # his own workspace is untouched
    assert (await c.post("/v1/login", json={"email": "petr@acme.dev", "password": PW,
                                            "org_id": petr["org"]["org_id"]})).status_code == 200


async def test_what_the_provider_may_not_do(c, app) -> None:  # noqa: ANN001
    olga, made = await _workspace(c, app)
    h = _idp(made["token"])
    # someone at another domain
    r = await c.post("/scim/v2/Users", headers=h, json={"userName": "ceo@victim.dev"})
    assert r.status_code == 400 and r.json()["scimType"] == "invalidValue"
    # the owner is the way back in, never deprovisioned
    r = await c.delete(f"/scim/v2/Users/{olga['user']['user_id']}", headers=h)
    assert r.status_code == 409
    # a wrong token, no token
    assert (await c.get("/scim/v2/Users", headers=_idp("scim_nope"))).status_code == 401
    assert (await c.get("/scim/v2/Users")).status_code == 401
    # a new token: the old one stops
    org = olga["org"]["org_id"]
    new = (await c.post(f"/v1/orgs/{org}/scim/token",
                        headers={"Authorization": f"Bearer {olga['access_token']}"})).json()
    assert (await c.get("/scim/v2/Users", headers=h)).status_code == 401
    assert (await c.get("/scim/v2/Users", headers=_idp(new["token"]))).status_code == 200
    bad = await c.get("/scim/v2/Users", headers=_idp(new["token"]),
                      params={"filter": 'name.familyName co "x"'})
    assert bad.status_code == 400 and bad.json()["scimType"] == "invalidFilter"


async def test_provisioning_is_the_business_plans(c, app) -> None:  # noqa: ANN001
    _, made = await _workspace(c, app, tier="coding")
    assert made == {"status": 402}
    assert (await c.get("/scim/v2/ServiceProviderConfig")).json()["patch"]["supported"] is True


async def test_a_deprovisioned_person_s_keys_are_revoked_on_the_plane(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AXOR_IDENTITY_PLANE_URL", "https://plane.acme.dev")
    monkeypatch.setenv("AXOR_IDENTITY_PLANE_TOKEN", "plane-master")
    seen: list[tuple[str, str, str]] = []

    def plane(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers.get("authorization", "")))
        return httpx.Response(200, json={"revoked": ["ak_1"]})

    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/p.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={}, public_url=PUBLIC, mailer=False,
                             oauth_transport=httpx.MockTransport(plane))
    async with application.router.lifespan_context(application), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://identity") as c:
        _, made = await _workspace(c, application)
        h = _idp(made["token"])
        made_anna = await c.post("/scim/v2/Users", headers=h, json={"userName": "anna@acme.dev"})
        anna = made_anna.json()
        await c.delete(f"/scim/v2/Users/{anna['id']}", headers=h)
    assert seen == [("DELETE", f"https://plane.acme.dev/v1/keys/of/{anna['id']}",
                     "Bearer plane-master")]
