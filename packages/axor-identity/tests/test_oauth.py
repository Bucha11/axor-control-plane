"""Sign in with GitHub / Google against a fake provider: the redirect dance,
linking by provider id and by verified address, refusing what is unsafe."""
from __future__ import annotations

import pathlib
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from axor_identity.app import create_app
from axor_identity.keys import generate_pem, load_signing_key
from axor_identity.oauth import Provider, providers_from_env

PUBLIC = "https://cp.example.com/identity"
PW = "correct horse battery"


class FakeGitHub:
    """GitHub's token and user endpoints, answering for whoever ``me`` is."""

    def __init__(self) -> None:
        self.me = {"id": 1001, "login": "octo"}
        self.emails = [{"email": "Octo@Example.com", "primary": True, "verified": True}]
        self.seen_verifier = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            form = parse_qs(request.content.decode())
            self.seen_verifier = form["code_verifier"][0]
            if form["code"] == ["good"]:
                return httpx.Response(200, json={"access_token": "gho_x"})
            return httpx.Response(200, json={"error": "bad_verification_code"})
        if request.url.path == "/user":
            return httpx.Response(200, json=self.me)
        if request.url.path == "/user/emails":
            return httpx.Response(200, json=self.emails)
        return httpx.Response(404)


GITHUB = Provider("github", "GitHub", "cid", "secret",
                  "https://github.com/login/oauth/authorize",
                  "https://github.com/login/oauth/access_token", "read:user user:email")


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub()


async def _app(tmp_path: pathlib.Path, fake: FakeGitHub, signup: str = "open"):  # noqa: ANN202
    application = create_app(
        f"sqlite+aiosqlite:///{tmp_path}/id.db",
        signing_key=load_signing_key(generate_pem(), kid="k"), signup=signup,
        providers={"github": GITHUB}, public_url=PUBLIC,
        return_origins=["https://lab.example.com"],
        oauth_transport=httpx.MockTransport(fake))
    return application


@pytest.fixture
async def client(tmp_path: pathlib.Path, fake: FakeGitHub):  # noqa: ANN201
    application = await _app(tmp_path, fake)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                     base_url="http://identity") as c:
            yield c


async def _sign_in(client: httpx.AsyncClient, code: str = "good",
                   return_to: str = "/#settings") -> dict:
    """start → (provider) → callback; returns the query of the final redirect."""
    start = await client.get("/v1/oauth/github/start", params={"return_to": return_to})
    assert start.status_code == 302, start.text
    auth = parse_qs(urlsplit(start.headers["location"]).query)
    assert auth["redirect_uri"] == [f"{PUBLIC}/v1/oauth/github/callback"]
    assert auth["code_challenge_method"] == ["S256"]
    back = await client.get("/v1/oauth/github/callback",
                            params={"state": auth["state"][0], "code": code})
    assert back.status_code == 302, back.text
    loc = urlsplit(back.headers["location"])
    return {"loc": loc, **{k: v[0] for k, v in parse_qs(loc.query).items()},
            "state": auth["state"][0]}


async def test_a_new_github_user_gets_an_account_and_a_workspace(client, fake) -> None:  # noqa: ANN001
    got = await _sign_in(client)
    # back to the app, the code before its #route
    assert got["loc"].netloc == "cp.example.com" and got["loc"].fragment == "settings"
    assert fake.seen_verifier  # PKCE verifier reached the token endpoint
    r = await client.post("/v1/oauth/exchange", json={"code": got["axor_login"]})
    assert r.status_code == 200, r.text
    session = r.json()
    assert session["created"] is True and session["user"]["email"] == "octo@example.com"
    assert session["org"]["role"] == "owner"
    me = (await client.get("/v1/me", headers={
        "Authorization": f"Bearer {session['access_token']}"})).json()
    assert me["user"]["providers"] == ["github"] and me["user"]["email_verified"]
    assert me["user"]["has_password"] is False
    assert me["memberships"][0]["name"] == "octo's workspace"
    # the code is single use, and so is the state
    again = await client.post("/v1/oauth/exchange", json={"code": got["axor_login"]})
    assert again.status_code == 401
    replay = await client.get("/v1/oauth/github/callback",
                              params={"state": got["state"], "code": "good"})
    assert replay.status_code == 400


async def test_the_second_sign_in_lands_on_the_same_account_even_after_a_new_address(
        client, fake) -> None:  # noqa: ANN001
    first = await _sign_in(client)
    a = (await client.post("/v1/oauth/exchange", json={"code": first["axor_login"]})).json()
    fake.emails = [{"email": "new@example.com", "primary": True, "verified": True}]
    second = await _sign_in(client)
    b = (await client.post("/v1/oauth/exchange", json={"code": second["axor_login"]})).json()
    assert b["created"] is False and b["user"]["user_id"] == a["user"]["user_id"]


async def test_a_password_account_is_linked_and_a_squatters_password_dropped(
        client, fake) -> None:  # noqa: ANN001
    # someone registered octo@example.com with a password before its owner came
    r = await client.post("/v1/signup", json={
        "email": "octo@example.com", "password": PW, "org_name": "Squat"})
    squatter_refresh = r.json()["refresh_token"]
    got = await _sign_in(client)
    s = (await client.post("/v1/oauth/exchange", json={"code": got["axor_login"]})).json()
    assert s["created"] is False and s["org"]["org_id"] == r.json()["org"]["org_id"]
    # the password no longer opens it, and its earlier sessions are dead
    login = await client.post("/v1/login", json={"email": "octo@example.com", "password": PW})
    assert login.status_code == 401
    refresh = await client.post("/v1/refresh", json={"refresh_token": squatter_refresh})
    assert refresh.status_code == 401


async def test_an_unverified_address_is_refused(client, fake) -> None:  # noqa: ANN001
    fake.emails = [{"email": "octo@example.com", "primary": True, "verified": False}]
    got = await _sign_in(client)
    assert "axor_login" not in got and "verified" in got["axor_login_error"]


async def test_a_refused_code_and_a_cancel_come_back_as_an_error(client, fake) -> None:  # noqa: ANN001
    got = await _sign_in(client, code="bad")
    assert "GitHub refused" in got["axor_login_error"]
    start = await client.get("/v1/oauth/github/start", params={"return_to": "/"})
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    back = await client.get("/v1/oauth/github/callback",
                            params={"state": state, "error": "access_denied"})
    assert "cancelled" in parse_qs(urlsplit(back.headers["location"]).query)["axor_login_error"][0]


@pytest.mark.parametrize("target", [
    "https://evil.example.org/", "//evil.example.org/", "/\\evil.example.org",
    "javascript:alert(1)",
])
async def test_return_to_must_be_ours(client, target: str) -> None:  # noqa: ANN001
    r = await client.get("/v1/oauth/github/start", params={"return_to": target})
    assert r.status_code == 400


async def test_another_listed_origin_is_allowed(client) -> None:  # noqa: ANN001
    got = await _sign_in(client, return_to="https://lab.example.com/login")
    assert got["loc"].netloc == "lab.example.com" and got["axor_login"]


async def test_a_closed_install_signs_in_members_but_makes_no_one_new(
        tmp_path: pathlib.Path, fake) -> None:  # noqa: ANN001
    application = await _app(tmp_path, fake, signup="closed")
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                     base_url="http://identity") as c:
            got = await _sign_in(c)
            assert "does not take new ones" in got["axor_login_error"]


async def test_providers_need_id_secret_and_a_public_url(
        tmp_path: pathlib.Path, fake) -> None:  # noqa: ANN001
    env = {"AXOR_IDENTITY_GITHUB_CLIENT_ID": "x", "AXOR_IDENTITY_GITHUB_CLIENT_SECRET": "y",
           "AXOR_IDENTITY_GOOGLE_CLIENT_ID": "only-half"}
    assert list(providers_from_env(env)) == ["github"]
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/x.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={"github": GITHUB}, public_url="")
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),
                                     base_url="http://identity") as c:
            assert (await c.get("/v1/oauth/providers")).json() == {"providers": []}
            assert (await c.get("/v1/oauth/github/start")).status_code == 404


async def test_the_provider_list_is_published(client) -> None:  # noqa: ANN001
    r = await client.get("/v1/oauth/providers")
    assert r.json() == {"providers": [{"name": "github", "label": "GitHub"}]}
