"""Who may do what to the team's agents: owners and admins always, viewers
never, members as the workspace says — and named people, whatever their role.
The defaults are what the plane did before; each change is on the audit trail."""
from __future__ import annotations

import base64
import pathlib
import time
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
from axor_backend.app import create_app
from axor_backend.ee.license import License
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KID = "k1"
ORG = "org_team"


def _jwks(priv: Ed25519PrivateKey) -> dict:
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": x, "use": "sig",
                      "alg": "EdDSA", "kid": KID}]}


@pytest.fixture
def priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
async def c(tmp_path: pathlib.Path, priv: Ed25519PrivateKey) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db",
                     operator_keys={}, allow_unsigned=True, api_token="master-x",
                     identity_jwks=_jwks(priv))
    async with app.router.lifespan_context(app), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://cp.test") as client:
        # who-may is the Business plan's
        app.state.licenses[ORG] = License(ORG, "business", 1, "2999-01-01")
        client.app = app  # type: ignore[attr-defined]
        yield client


@pytest.fixture
def who(priv: Ed25519PrivateKey):  # noqa: ANN201
    def as_(email: str, role: str) -> dict:
        now = int(time.time())
        token = jwt.encode({"iss": "axor-identity", "sub": f"usr_{email}", "eml": email,
                            "org": ORG, "role": role, "tier": "community",
                            "iat": now, "exp": now + 900}, priv, algorithm="EdDSA",
                           headers={"kid": KID})
        return {"Authorization": f"Bearer {token}"}
    return as_


async def _session(c: httpx.AsyncClient, h: dict, node: str) -> None:
    r = await c.post(f"/v1/plane/{node}/profile", headers=h,
                     json={"kind": "coding", "agent": "claude-code", "developer": "x"})
    assert r.status_code < 300, r.text
    await c.post(f"/v1/plane/{node}/telemetry", headers=h, json={"run_id": f"{node}-hb", "events": [
        {"seq": 0, "kind": "heartbeat", "payload": {"applied_version": 0, "level": "NORMAL",
                                                    "budget_remaining": None}}]})


async def test_a_lead_stops_a_teammates_session_once_let(c: httpx.AsyncClient, who) -> None:  # noqa: ANN001
    ivan, petr = who("ivan@team.dev", "member"), who("petr@team.dev", "member")
    olga = who("olga@team.dev", "admin")
    await _session(c, ivan, "claude-code-ivan-1")
    stop = {"version": 1, "state": {"stopped": True}}
    r = await c.post("/v1/plane/claude-code-ivan-1/command", json=stop, headers=petr)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_your_session"
    # what petr may, before and after: the screens offer only that
    assert (await c.get("/v1/permissions", headers=petr)).json()["you"]["stop_others"] is False
    r = await c.put("/v1/permissions", headers=olga,
                    json={"stop_others": {"people": ["Petr@team.dev"]}})
    assert r.status_code == 200
    assert r.json()["capabilities"]["stop_others"]["people"] == ["petr@team.dev"]
    assert (await c.get("/v1/permissions", headers=petr)).json()["you"]["stop_others"] is True
    assert (await c.post("/v1/plane/claude-code-ivan-1/command", json=stop,
                         headers=petr)).status_code == 202
    # a member cannot give it themselves
    assert (await c.put("/v1/permissions", headers=ivan,
                        json={"stop_others": {"members": True}})).status_code == 403
    trail = (await c.get("/v1/audit", headers=olga)).json()["items"]
    granted = next(t for t in trail if t["action"] == "permission_granted")
    assert granted["target"] == "stop_others" and granted["detail"] == {"to": "petr@team.dev"}
    assert granted["who"] == "olga@team.dev"
    # the month's report names it: who acted on whose session
    report = (await c.get("/v1/audit/report", headers=olga)).json()
    assert report["on_others_sessions"] == [{
        "ts": report["on_others_sessions"][0]["ts"], "who": "petr@team.dev", "action": "stop",
        "session": "claude-code-ivan-1", "developer": "ivan@team.dev"}]


async def test_the_brake_and_exceptions_for_whoever_is_let(c: httpx.AsyncClient, who) -> None:  # noqa: ANN001
    petr, olga = who("petr@team.dev", "member"), who("olga@team.dev", "owner")
    assert (await c.put("/v1/coding/brake", json={"on": True}, headers=petr)).status_code == 403
    exc = {"exceptions": [{"rule": "secrets", "path": "tests/fixtures/*"}]}
    assert (await c.put("/v1/coding/exceptions", json=exc, headers=petr)).status_code == 403
    await c.put("/v1/permissions", headers=olga,
                json={"brake": {"members": True}, "exceptions": {"people": ["petr@team.dev"]}})
    assert (await c.put("/v1/coding/brake", json={"on": True, "reason": "incident"},
                        headers=petr)).status_code == 200
    r = await c.put("/v1/coding/exceptions", json=exc, headers=petr)
    assert r.status_code == 200 and r.json()["exceptions"][0]["by"] == "petr@team.dev"
    # the rules themselves stay the admin's
    assert (await c.put("/v1/coding/rules", json={"allow": []}, headers=petr)).status_code == 403


async def test_skills_and_memory_can_be_taken_from_members(c: httpx.AsyncClient, who) -> None:  # noqa: ANN001
    petr, olga = who("petr@team.dev", "member"), who("olga@team.dev", "admin")
    # members may by default — as before
    off = await c.put("/v1/coding/skills/pdf", json={"off": True}, headers=petr)
    assert off.status_code == 200
    await c.put("/v1/permissions", headers=olga, json={"skills": {"members": False},
                                                       "memory": {"members": False}})
    r = await c.put("/v1/coding/skills/pdf", json={"off": False}, headers=petr)
    assert r.status_code == 403 and r.json()["detail"]["capability"] == "skills"
    assert (await c.put("/v1/memory/settings", json={"fresh_days": 10},
                        headers=petr)).status_code == 403
    # an admin always may
    on = await c.put("/v1/coding/skills/pdf", json={"off": False}, headers=olga)
    assert on.status_code == 200
    # a viewer never — whatever is granted to members
    viewer = who("vera@team.dev", "viewer")
    assert (await c.get("/v1/permissions", headers=viewer)).json()["you"] == {
        k: False for k in ("stop_others", "brake", "exceptions", "skills", "memory", "production")}


async def test_bad_permissions_are_refused(c: httpx.AsyncClient, who) -> None:  # noqa: ANN001
    olga = who("olga@team.dev", "admin")
    for body in ({}, {"fly": {"members": True}}, {"brake": {"members": "yes"}},
                 {"brake": {"people": ["not-an-email"]}}):
        assert (await c.put("/v1/permissions", json=body, headers=olga)).status_code == 400


async def test_below_business_the_defaults_hold_and_cannot_be_changed(
        c: httpx.AsyncClient, who) -> None:  # noqa: ANN001
    del c.app.state.licenses[ORG]  # type: ignore[attr-defined]
    olga = who("olga@team.dev", "admin")
    r = await c.put("/v1/permissions", headers=olga, json={"brake": {"members": True}})
    assert r.status_code == 402 and r.json()["detail"]["plan"] == "business"
    got = (await c.get("/v1/permissions", headers=olga)).json()
    assert got["editable"] is False and got["capabilities"]["skills"]["members"] is True


async def test_the_login_service_tells_the_trail_who_joined_and_left(
        c: httpx.AsyncClient, who, priv: Ed25519PrivateKey) -> None:  # noqa: ANN001
    """Acts on people are recorded by the login service — with its own token,
    signed with the key this plane trusts. A workspace admin cannot write
    them: the trail would no longer say what happened."""
    now = int(time.time())
    svc = {"Authorization": "Bearer " + jwt.encode(
        {"iss": "axor-identity", "sub": "svc_identity", "eml": "identity@axor", "org": ORG,
         "role": "admin", "tier": "community", "iat": now, "exp": now + 120},
        priv, algorithm="EdDSA", headers={"kid": KID})}
    act = {"action": "scim_removed", "target": "anna@acme.dev",
           "who": "your identity provider (SCIM)", "detail": {"role": "member", "secret": "x"}}
    assert (await c.post("/v1/audit/identity", headers=who("olga@acme.dev", "owner"),
                         json=act)).status_code == 403
    assert (await c.post("/v1/audit/identity", headers=svc,
                         json={**act, "action": "drop_table"})).status_code == 400
    assert (await c.post("/v1/audit/identity", headers=svc, json=act)).status_code == 204
    trail = (await c.get("/v1/audit", headers=who("olga@acme.dev", "owner"))).json()
    row = trail["items"][0]
    assert (row["action"], row["target"], row["who"]) == (
        "scim_removed", "anna@acme.dev", "your identity provider (SCIM)")
    assert row["detail"] == {"role": "member"}            # only what an act on people says
    month = row["ts"][:7]
    report = (await c.get("/v1/audit/report", headers=who("olga@acme.dev", "owner"),
                          params={"month": month})).json()
    assert [r["action"] for r in report["access_changes"]] == ["scim_removed"]
    # QA-134: the row carries the role of who acted, not the login service's
    # own "admin"; a viewer joining reads viewer, an act with no role none
    assert "role" not in row or row["role"] != "admin"
    assert (await c.post("/v1/audit/identity", headers=svc, json={
        "action": "member_added", "target": "vic@acme.dev", "who": "vic@acme.dev",
        "role": "viewer", "detail": {"role": "viewer", "invited": True}})).status_code == 204
    assert (await c.post("/v1/audit/identity", headers=svc, json={
        "action": "plan_upgraded", "target": "team", "who": "license renewal",
        "detail": {"tier": "team"}})).status_code == 204
    rows = (await c.get("/v1/audit", headers=who("olga@acme.dev", "owner"))).json()["items"]
    joined = next(r for r in rows if r["action"] == "member_added")
    assert joined.get("role") == "viewer"
    assert any(r["action"] == "plan_upgraded" for r in rows)
