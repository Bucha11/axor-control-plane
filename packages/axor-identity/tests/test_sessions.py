"""A workspace's session policy: everyone signs in again after so many hours,
counted from the sign-in however often it refreshed — and an admin can end
every sign-in at once."""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_identity.app import create_app
from axor_identity.keys import generate_pem, load_signing_key
from axor_identity.storage import refresh_tokens
from sqlalchemy import update

PW = "correct horse battery"


@pytest.fixture
async def app(tmp_path: pathlib.Path):  # noqa: ANN201
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/s.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={}, public_url="https://cp.example.com/identity",
                             mailer=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def c(app):  # noqa: ANN001, ANN201
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://identity") as client:
        yield client


async def _team(c: httpx.AsyncClient, app) -> tuple[dict, dict]:  # noqa: ANN001
    olga = (await c.post("/v1/signup", json={"email": "olga@acme.dev", "password": PW,
                                             "org_name": "Acme"})).json()
    org = olga["org"]["org_id"]
    await app.state.store.set_org_tier(org, "business", "2999-01-01")
    petr = (await c.post("/v1/signup", json={"email": "petr@acme.dev", "password": PW,
                                             "org_name": "Petr's"})).json()
    await app.state.store.add_membership(petr["user"]["user_id"], org, "member",
                                         "2026-01-01T00:00:00Z")
    petr = (await c.post("/v1/login", json={"email": "petr@acme.dev", "password": PW,
                                            "org_id": org})).json()
    return olga, petr


def _h(who: dict) -> dict:
    return {"Authorization": f"Bearer {who['access_token']}"}


async def _signed_in_ago(app, hours: int) -> None:  # noqa: ANN001
    async with app.state.store.engine.begin() as conn:
        import datetime

        ts = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=hours)) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")
        await conn.execute(update(refresh_tokens).values(session_ts=ts))


async def test_a_sign_in_lasts_as_long_as_the_workspace_says(c, app) -> None:  # noqa: ANN001
    olga, petr = await _team(c, app)
    org = olga["org"]["org_id"]
    assert petr["org"]["org_id"] == org
    got = (await c.get(f"/v1/orgs/{org}/sessions", headers=_h(olga))).json()
    assert got["hours"] is None and got["plan_ok"] is True
    r = await c.put(f"/v1/orgs/{org}/sessions", headers=_h(olga), json={"hours": 8})
    assert r.status_code == 200 and r.json()["hours"] == 8
    # a refresh within the window rotates and keeps the sign-in's time
    r = await c.post("/v1/refresh", json={"refresh_token": petr["refresh_token"]})
    assert r.status_code == 200
    fresh = r.json()["refresh_token"]
    await _signed_in_ago(app, 9)
    r = await c.post("/v1/refresh", json={"refresh_token": fresh})
    assert r.status_code == 401 and "every 8 hours" in r.json()["detail"]
    # signing in again starts a new window
    again = (await c.post("/v1/login", json={"email": "petr@acme.dev", "password": PW,
                                             "org_id": org})).json()
    assert (await c.post("/v1/refresh",
                         json={"refresh_token": again["refresh_token"]})).status_code == 200


async def test_the_policy_is_an_admin_s_and_the_business_plan_s(c, app) -> None:  # noqa: ANN001
    olga, petr = await _team(c, app)
    org = olga["org"]["org_id"]
    assert (await c.put(f"/v1/orgs/{org}/sessions", headers=_h(petr),
                        json={"hours": 8})).status_code == 403
    assert (await c.put(f"/v1/orgs/{org}/sessions", headers=_h(olga),
                        json={"hours": 0})).status_code == 422
    await app.state.store.set_org_tier(org, "coding", "2999-01-01")
    r = await c.put(f"/v1/orgs/{org}/sessions", headers=_h(olga), json={"hours": 8})
    assert r.status_code == 402 and r.json()["detail"]["plan"] == "business"


async def test_an_admin_ends_everyone_s_sign_in_but_their_own(c, app) -> None:  # noqa: ANN001
    olga, petr = await _team(c, app)
    org = olga["org"]["org_id"]
    # on every plan: ending sessions is not a premium feature
    await app.state.store.set_org_tier(org, "community", None)
    assert (await c.post(f"/v1/orgs/{org}/sessions/end", headers=_h(petr))).status_code == 403
    r = await c.post(f"/v1/orgs/{org}/sessions/end", headers=_h(olga))
    assert r.status_code == 200 and r.json()["ended"] == 1
    assert (await c.post("/v1/refresh",
                         json={"refresh_token": petr["refresh_token"]})).status_code == 401
    assert (await c.post("/v1/refresh",
                         json={"refresh_token": olga["refresh_token"]})).status_code == 200


async def test_a_removed_member_s_sign_in_ends_at_once(c, app) -> None:  # noqa: ANN001
    olga, petr = await _team(c, app)
    org = olga["org"]["org_id"]
    r = await c.delete(f"/v1/orgs/{org}/members/{petr['user']['user_id']}", headers=_h(olga))
    assert r.status_code == 200
    assert (await c.post("/v1/refresh",
                         json={"refresh_token": petr["refresh_token"]})).status_code == 401


async def test_a_removed_member_is_refused_on_the_org_routes_at_once(c, app) -> None:  # noqa: ANN001
    """QA-154: after removal the old access token still listed the team's
    e-mail addresses until it expired. The org routes read the membership
    now, and say in words that the person is no longer in the workspace."""
    olga, petr = await _team(c, app)
    org = olga["org"]["org_id"]
    assert (await c.get(f"/v1/orgs/{org}/members", headers=_h(petr))).status_code == 200
    await c.delete(f"/v1/orgs/{org}/members/{petr['user']['user_id']}", headers=_h(olga))
    r = await c.get(f"/v1/orgs/{org}/members", headers=_h(petr))
    assert r.status_code == 403
    assert r.json()["detail"]["detail"] == "you are no longer a member of Acme"
    r = await c.post("/v1/refresh", json={"refresh_token": petr["refresh_token"]})
    assert r.status_code == 401 and r.json()["detail"] == "you are no longer a member of Acme"
    # the feed tells the control plane, naming nobody
    from axor_identity.storage import member_key
    feed = (await c.get("/v1/revocations")).json()
    assert {"key": member_key(petr["user"]["user_id"], org), "epoch": 1,
            "reason": "removed"} in feed["members"]
    assert "petr" not in str(feed)


async def test_a_role_downgrade_is_the_role_used_at_once(c, app) -> None:  # noqa: ANN001
    """QA-154, role change: an admin made viewer kept the admin's token. The
    org routes use the role held now; the next token carries it and the new
    epoch, and the feed bumps the old one."""
    olga, petr = await _team(c, app)
    org = olga["org"]["org_id"]
    uid = petr["user"]["user_id"]
    await c.put(f"/v1/orgs/{org}/members/{uid}", json={"role": "admin"}, headers=_h(olga))
    admin = (await c.post("/v1/login", json={"email": "petr@acme.dev", "password": PW,
                                             "org_id": org})).json()
    assert (await c.get(f"/v1/orgs/{org}/invites", headers=_h(admin))).status_code == 200
    await c.put(f"/v1/orgs/{org}/members/{uid}", json={"role": "viewer"}, headers=_h(olga))
    assert (await c.get(f"/v1/orgs/{org}/invites", headers=_h(admin))).status_code == 403
    fresh = (await c.post("/v1/refresh", json={"refresh_token": admin["refresh_token"]})).json()
    from axor_identity.verify import verify_access_token
    jwks = (await c.get("/.well-known/jwks.json")).json()
    claims = verify_access_token(fresh["access_token"], jwks)
    assert claims.role == "viewer" and claims.epoch == 2
    assert claims.session_id == verify_access_token(admin["access_token"], jwks).session_id
