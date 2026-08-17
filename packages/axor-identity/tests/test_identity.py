"""The identity service end to end: signup, login, refresh rotation, logout,
membership, and that its access tokens verify against the published JWKS with
the shared client (the same path the Lab and control-plane will use).
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_identity.app import create_app
from axor_identity.keys import SigningKey, generate_pem, load_signing_key
from axor_identity.tokens import issue_access
from axor_identity.verify import IdentityError, verify_access_token

PW = "correct horse battery"


def _client(app) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://identity.io")


@pytest.fixture
def signing_key() -> SigningKey:
    return load_signing_key(generate_pem(), kid="test-kid")


@pytest.fixture
async def app(tmp_path: pathlib.Path, signing_key: SigningKey):  # noqa: ANN201
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/id.db",
                             signing_key=signing_key)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app) -> httpx.AsyncClient:  # noqa: ANN001
    async with _client(app) as c:
        yield c


async def _signup(client: httpx.AsyncClient, email: str, org: str = "Acme") -> dict:
    r = await client.post("/v1/signup",
                          json={"email": email, "password": PW, "org_name": org})
    assert r.status_code == 201, r.text
    return r.json()


# ── signup & me ───────────────────────────────────────────────────────────────
async def test_signup_issues_a_session_and_me_reflects_it(
        client: httpx.AsyncClient) -> None:
    session = await _signup(client, "ada@acme.io")
    assert session["org"]["role"] == "owner"
    assert session["org"]["tier"] == "community"
    me = await client.get("/v1/me",
                          headers={"Authorization": f"Bearer {session['access_token']}"})
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == "ada@acme.io"
    assert body["active_org"] == session["org"]["org_id"]
    assert len(body["memberships"]) == 1


async def test_duplicate_email_is_409(client: httpx.AsyncClient) -> None:
    await _signup(client, "dup@acme.io")
    r = await client.post("/v1/signup",
                         json={"email": "DUP@acme.io", "password": PW,
                               "org_name": "Other"})
    assert r.status_code == 409  # email normalized to lower-case


# ── login ─────────────────────────────────────────────────────────────────────
async def test_login_success_and_token_verifies_against_jwks(
        client: httpx.AsyncClient) -> None:
    await _signup(client, "grace@acme.io")
    r = await client.post("/v1/login",
                         json={"email": "grace@acme.io", "password": PW})
    assert r.status_code == 200
    access = r.json()["access_token"]
    jwks = (await client.get("/.well-known/jwks.json")).json()
    claims = verify_access_token(access, jwks)  # the shared client
    assert claims.email == "grace@acme.io"
    assert claims.role == "owner"
    assert claims.tier == "community"


async def test_wrong_password_and_unknown_email_are_401(
        client: httpx.AsyncClient) -> None:
    await _signup(client, "real@acme.io")
    assert (await client.post("/v1/login",
            json={"email": "real@acme.io", "password": "nope"})).status_code == 401
    assert (await client.post("/v1/login",
            json={"email": "ghost@acme.io", "password": PW})).status_code == 401


# ── refresh rotation ──────────────────────────────────────────────────────────
async def test_refresh_rotates_and_old_token_dies(client: httpx.AsyncClient) -> None:
    session = await _signup(client, "rot@acme.io")
    first = session["refresh_token"]
    r = await client.post("/v1/refresh", json={"refresh_token": first})
    assert r.status_code == 200
    second = r.json()
    assert second["refresh_token"] != first
    # the rotated-out token is single-use — replay is refused
    assert (await client.post("/v1/refresh",
            json={"refresh_token": first})).status_code == 401
    # the new refresh works, and its access token verifies
    jwks = (await client.get("/.well-known/jwks.json")).json()
    verify_access_token(second["access_token"], jwks)
    assert (await client.post("/v1/refresh",
            json={"refresh_token": second["refresh_token"]})).status_code == 200


async def test_logout_revokes_the_refresh_token(client: httpx.AsyncClient) -> None:
    session = await _signup(client, "bye@acme.io")
    assert (await client.post("/v1/logout",
            json={"refresh_token": session["refresh_token"]})).status_code == 204
    assert (await client.post("/v1/refresh",
            json={"refresh_token": session["refresh_token"]})).status_code == 401


# ── multi-org membership ──────────────────────────────────────────────────────
async def test_membership_across_orgs_and_org_selection(
        client: httpx.AsyncClient) -> None:
    user_a = await _signup(client, "multi@acme.io", org="OrgA")
    owner_b = await _signup(client, "ownerb@beta.io", org="OrgB")
    org_b = owner_b["org"]["org_id"]
    # OrgB's owner adds the first user as a member of OrgB
    add = await client.post(
        f"/v1/orgs/{org_b}/members",
        json={"email": "multi@acme.io", "role": "member"},
        headers={"Authorization": f"Bearer {owner_b['access_token']}"})
    assert add.status_code == 201, add.text
    # now that user belongs to two orgs → login must disambiguate
    ambiguous = await client.post("/v1/login",
                                 json={"email": "multi@acme.io", "password": PW})
    assert ambiguous.status_code == 400
    # choosing OrgB yields the member role and OrgB's tier
    chosen = await client.post(
        "/v1/login",
        json={"email": "multi@acme.io", "password": PW, "org_id": org_b})
    assert chosen.status_code == 200
    assert chosen.json()["org"]["role"] == "member"
    _ = user_a  # (OrgA session unused beyond establishing the first membership)


async def test_add_member_requires_admin_of_that_org(
        client: httpx.AsyncClient) -> None:
    owner = await _signup(client, "owner@gamma.io", org="Gamma")
    org = owner["org"]["org_id"]
    await _signup(client, "outsider@delta.io", org="Delta")
    # an access token for a DIFFERENT org cannot add members to Gamma
    outsider_login = await client.post(
        "/v1/login", json={"email": "outsider@delta.io", "password": PW})
    outsider_access = outsider_login.json()["access_token"]
    r = await client.post(
        f"/v1/orgs/{org}/members",
        json={"email": "outsider@delta.io", "role": "member"},
        headers={"Authorization": f"Bearer {outsider_access}"})
    assert r.status_code == 403


# ── token verification edge cases ─────────────────────────────────────────────
async def test_me_rejects_missing_and_tampered_tokens(
        client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/me")).status_code == 401
    session = await _signup(client, "tamper@acme.io")
    tampered = session["access_token"][:-4] + "AAAA"
    assert (await client.get(
        "/v1/me", headers={"Authorization": f"Bearer {tampered}"})).status_code == 401


async def test_expired_access_token_is_refused(
        client: httpx.AsyncClient, signing_key: SigningKey) -> None:
    session = await _signup(client, "old@acme.io")
    expired = issue_access(signing_key, user_id=session["user"]["user_id"],
                           email="old@acme.io", org=session["org"]["org_id"],
                           role="owner", tier="community", ttl=-3600)
    r = await client.get("/v1/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


def test_verifier_rejects_a_token_signed_by_a_different_key() -> None:
    """A token from an untrusted signer must not verify against our JWKS."""
    ours = load_signing_key(generate_pem(), kid="ours")
    attacker = load_signing_key(generate_pem(), kid="ours")  # same kid, wrong key
    forged = issue_access(attacker, user_id="u", email="e@x.io", org="o",
                          role="owner", tier="community", ttl=900)
    with pytest.raises(IdentityError):
        verify_access_token(forged, ours.jwks())
