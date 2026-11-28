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
    # QA-048: sign-up says an address has an account (it signs in at once, so
    # it cannot answer like a success) — and says what to do instead
    assert r.json()["detail"] == ("that address already has an account — log in, "
                                  "or reset the password if you forgot it")


# QA-230: a 300-character workspace name answered 500 and left the user
# created without a workspace — sign-up then said "exists", log-in "no
# organization": the address was stuck.
async def test_a_too_long_workspace_name_is_a_400_in_words_and_writes_nothing(
        client: httpx.AsyncClient, app) -> None:  # noqa: ANN001
    r = await client.post("/v1/signup", json={"email": "long@acme.io", "password": PW,
                                              "org_name": "W" * 300})
    assert r.status_code == 400
    assert r.json()["detail"] == "a workspace name is at most 100 characters (this one has 300)"
    assert await app.state.store.get_user_by_email("long@acme.io") is None
    r = await client.post("/v1/signup", json={"email": "long@acme.io", "password": "x" * 2000,
                                              "org_name": "Acme"})
    assert r.status_code == 400 and "password is at most" in r.json()["detail"]
    await _signup(client, "long@acme.io", org="W" * 100)


async def test_a_sign_up_that_fails_half_way_leaves_no_account(
        client: httpx.AsyncClient, app, monkeypatch) -> None:  # noqa: ANN001
    from axor_identity import storage

    real = storage.insert

    def failing_insert(table):  # noqa: ANN001, ANN202 - the org insert breaks
        if table is storage.orgs:
            raise RuntimeError("the database refused the workspace")
        return real(table)

    monkeypatch.setattr(storage, "insert", failing_insert)
    with pytest.raises(RuntimeError):
        await client.post("/v1/signup", json={"email": "half@acme.io", "password": PW,
                                              "org_name": "Acme"})
    monkeypatch.setattr(storage, "insert", real)
    assert await app.state.store.get_user_by_email("half@acme.io") is None
    await _signup(client, "half@acme.io")


async def test_an_account_left_without_a_workspace_is_completed_by_signing_up_again(
        client: httpx.AsyncClient, app) -> None:  # noqa: ANN001
    from axor_identity.passwords import hash_password

    # what the old half-made sign-up left behind
    await app.state.store.create_user("usr_stuck", "stuck@acme.io", hash_password(PW),
                                      "2026-11-27T00:00:00Z")
    r = await client.post("/v1/login", json={"email": "stuck@acme.io", "password": PW})
    assert r.status_code == 403
    assert "sign up again with this address and password" in r.json()["detail"]["detail"]
    # someone else cannot take the address over with another password
    r = await client.post("/v1/signup", json={"email": "stuck@acme.io",
                                              "password": "another password",
                                              "org_name": "Mine"})
    assert r.status_code == 409
    done = await _signup(client, "stuck@acme.io", org="Stuck Inc")
    assert done["user"]["user_id"] == "usr_stuck" and done["org"]["role"] == "owner"
    r = await client.post("/v1/login", json={"email": "stuck@acme.io", "password": PW})
    assert r.status_code == 200
    # and once it has one, sign-up says "exists" as for anyone
    r = await client.post("/v1/signup", json={"email": "stuck@acme.io", "password": PW,
                                              "org_name": "Second"})
    assert r.status_code == 409


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
    # the new refresh works, and its access token verifies
    jwks = (await client.get("/.well-known/jwks.json")).json()
    verify_access_token(second["access_token"], jwks)
    third = await client.post("/v1/refresh", json={"refresh_token": second["refresh_token"]})
    assert third.status_code == 200
    other = (await client.post("/v1/login", json={"email": "rot@acme.io",
                                                  "password": PW})).json()
    # the rotated-out token is single-use — replay is refused...
    assert (await client.post("/v1/refresh",
            json={"refresh_token": first})).status_code == 401
    # ...and (QA-240) the replay ends the whole session: the token the
    # legitimate holder has now is refused too, and so is its access token
    assert (await client.post("/v1/refresh", json={
        "refresh_token": third.json()["refresh_token"]})).status_code == 401
    me = await client.get("/v1/me", headers={
        "Authorization": f"Bearer {third.json()['access_token']}"})
    assert me.status_code == 401
    # another sign-in of the same person is not touched
    assert (await client.post("/v1/refresh", json={
        "refresh_token": other["refresh_token"]})).status_code == 200


async def test_a_duplicated_tab_presenting_the_just_rotated_token_gets_the_same_pair(
        client: httpx.AsyncClient) -> None:
    # QA-480: a duplicated tab holds a copy of the refresh token. Tab 1 rotates
    # it; tab 2 presenting its copy a moment later must not end the session in
    # both tabs — it gets the successor tab 1 got.
    session = await _signup(client, "dup@acme.io")
    old = session["refresh_token"]
    one = await client.post("/v1/refresh", json={"refresh_token": old})
    two = await client.post("/v1/refresh", json={"refresh_token": old})
    assert one.status_code == 200 and two.status_code == 200, two.text
    assert two.json()["refresh_token"] == one.json()["refresh_token"]
    me = await client.get("/v1/me", headers={
        "Authorization": f"Bearer {one.json()['access_token']}"})
    assert me.status_code == 200
    # the shared successor keeps rotating as usual
    nxt = await client.post("/v1/refresh", json={"refresh_token": one.json()["refresh_token"]})
    assert nxt.status_code == 200
    # once the successor has moved on, the old copy is reuse again: the session ends
    assert (await client.post("/v1/refresh", json={"refresh_token": old})).status_code == 401
    assert (await client.post("/v1/refresh", json={
        "refresh_token": nxt.json()["refresh_token"]})).status_code == 401


async def test_the_old_token_past_the_grace_window_is_reuse(
        app, client: httpx.AsyncClient) -> None:  # noqa: ANN001
    # QA-480: the grace is short — after it, reuse detection is as before (QA-240)
    from axor_identity.storage import refresh_tokens
    from sqlalchemy import update

    session = await _signup(client, "late@acme.io")
    old = session["refresh_token"]
    one = (await client.post("/v1/refresh", json={"refresh_token": old})).json()
    async with app.state.store.engine.begin() as conn:
        await conn.execute(update(refresh_tokens).values(created_ts="2000-01-01T00:00:00Z"))
    assert (await client.post("/v1/refresh", json={"refresh_token": old})).status_code == 401
    assert (await client.post("/v1/refresh", json={
        "refresh_token": one["refresh_token"]})).status_code == 401


async def test_logout_revokes_the_refresh_token(client: httpx.AsyncClient) -> None:
    session = await _signup(client, "bye@acme.io")
    h = {"Authorization": f"Bearer {session['access_token']}"}
    assert (await client.get("/v1/me", headers=h)).status_code == 200
    assert (await client.post("/v1/logout",
            json={"refresh_token": session["refresh_token"]})).status_code == 204
    assert (await client.post("/v1/refresh",
            json={"refresh_token": session["refresh_token"]})).status_code == 401
    # QA-240: the access token the tab still holds stops too, not in 15 minutes
    r = await client.get("/v1/me", headers=h)
    assert r.status_code == 401 and "signed out" in r.json()["detail"]
    feed = (await client.get("/v1/revocations")).json()
    assert len(feed["sessions"]) == 1 and feed["window"] > 900


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


# ── signup modes (AXOR_IDENTITY_SIGNUP) ───────────────────────────────────────
# A self-hosted install is single-tenant: one company runs it for itself. An open
# /v1/signup on a public hostname lets anyone passing by create an org on their
# server, so the mode has to be a real setting rather than an edge rule a
# deployment may or may not carry.


def _app_with_signup(tmp_path: pathlib.Path, signing_key: SigningKey, mode: str):  # noqa: ANN202
    return create_app(f"sqlite+aiosqlite:///{tmp_path}/id-{mode}.db",
                      signing_key=signing_key, signup=mode)


async def test_open_is_the_default_and_lets_anyone_sign_up(
        tmp_path: pathlib.Path, signing_key: SigningKey) -> None:
    """Unset behaves exactly as before the setting existed."""
    app = create_app(f"sqlite+aiosqlite:///{tmp_path}/id-default.db",
                     signing_key=signing_key)
    async with app.router.lifespan_context(app), _client(app) as c:
        assert (await _signup(c, "first@example.com"))["org"]["role"] == "owner"
        r = await c.post("/v1/signup", json={"email": "second@example.com",
                                             "password": PW, "org_name": "Other"})
        assert r.status_code == 201


async def test_closed_refuses_every_signup(
        tmp_path: pathlib.Path, signing_key: SigningKey) -> None:
    app = _app_with_signup(tmp_path, signing_key, "closed")
    async with app.router.lifespan_context(app), _client(app) as c:
        r = await c.post("/v1/signup", json={"email": "anyone@example.com",
                                             "password": PW, "org_name": "Acme"})
        assert r.status_code == 403


async def test_first_only_admits_one_account_then_shuts(
        tmp_path: pathlib.Path, signing_key: SigningKey) -> None:
    """The operator claims the install; the next stranger is refused."""
    app = _app_with_signup(tmp_path, signing_key, "first-only")
    async with app.router.lifespan_context(app), _client(app) as c:
        assert (await _signup(c, "owner@example.com"))["org"]["role"] == "owner"
        r = await c.post("/v1/signup", json={"email": "stranger@example.com",
                                             "password": PW, "org_name": "Theirs"})
        assert r.status_code == 403


async def test_closing_signup_does_not_close_the_install(
        tmp_path: pathlib.Path, signing_key: SigningKey) -> None:
    """Login and adding colleagues keep working — that is the whole point: the
    install is shut to strangers, not to the company running it."""
    app = _app_with_signup(tmp_path, signing_key, "first-only")
    async with app.router.lifespan_context(app), _client(app) as c:
        owner = await _signup(c, "owner@example.com")
        org = owner["org"]["org_id"]
        head = {"Authorization": f"Bearer {owner['access_token']}"}

        # login still works
        r = await c.post("/v1/login", json={"email": "owner@example.com",
                                            "password": PW})
        assert r.status_code == 200

        # a colleague who has no account cannot be added — signup is shut, so an
        # install in this mode grows by invitation only, and the 404 says so.
        r = await c.post(f"/v1/orgs/{org}/members",
                         json={"email": "colleague@example.com", "role": "member"},
                         headers=head)
        assert r.status_code == 404


async def test_signup_mode_is_published_so_a_client_can_hide_the_form(
        tmp_path: pathlib.Path, signing_key: SigningKey) -> None:
    app = _app_with_signup(tmp_path, signing_key, "first-only")
    async with app.router.lifespan_context(app), _client(app) as c:
        before = (await c.get("/v1/signup-mode")).json()
        assert before == {"mode": "first-only", "available": True}
        await _signup(c, "owner@example.com")
        after = (await c.get("/v1/signup-mode")).json()
        assert after == {"mode": "first-only", "available": False}


async def test_an_unknown_mode_fails_at_construction(
        tmp_path: pathlib.Path, signing_key: SigningKey) -> None:
    """A typo in a security setting must not degrade to 'open' in silence."""
    with pytest.raises(ValueError, match="AXOR_IDENTITY_SIGNUP"):
        create_app(f"sqlite+aiosqlite:///{tmp_path}/id-bad.db",
                   signing_key=signing_key, signup="disabled")
