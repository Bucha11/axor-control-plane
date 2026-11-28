"""Single sign-on per workspace, against a fake OpenID provider: the admin
sets it up for a domain they proved, a work address signs in through the
provider, the ID token is checked before anyone is let in, and a workspace that
enforces it keeps its people off passwords (its owner keeps one, the way back)."""
from __future__ import annotations

import json
import pathlib
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from axor_identity import sso
from axor_identity.app import create_app
from axor_identity.keys import generate_pem, load_signing_key
from cryptography.hazmat.primitives.asymmetric import rsa

PUBLIC = "https://cp.example.com/identity"
ISSUER = "https://idp.acme.dev"
PW = "correct horse battery"


class FakeIdP:
    """An OpenID provider: discovery, keys, and a token endpoint that signs an
    ID token for whoever ``person`` is."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**jwk, "kid": "idp-1", "use": "sig", "alg": "RS256"}]}
        self.person = {"sub": "okta-001", "email": "new@acme.dev", "email_verified": True}
        self.nonce = ""
        self.aud = "axor-client"
        self.issuer = ISSUER
        self.saw_auth = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/keys"})
        if path == "/keys":
            return httpx.Response(200, json=self.jwks)
        if path == "/token":
            self.saw_auth = request.headers.get("authorization", "")
            form = parse_qs(request.content.decode())
            if form.get("code") != ["good"]:
                return httpx.Response(400, json={"error": "invalid_grant"})
            now = int(time.time())
            token = jwt.encode({"iss": self.issuer, "aud": self.aud, "iat": now, "exp": now + 300,
                                "nonce": self.nonce, **self.person},
                               self.key, algorithm="RS256", headers={"kid": "idp-1"})
            return httpx.Response(200, json={"id_token": token, "access_token": "at"})
        return httpx.Response(404)


@pytest.fixture
def idp() -> FakeIdP:
    sso._discovery.clear()
    sso._jwks_cache.clear()
    return FakeIdP()


@pytest.fixture
async def app(tmp_path: pathlib.Path, idp: FakeIdP):  # noqa: ANN201
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/id.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={}, public_url=PUBLIC,
                             oauth_transport=httpx.MockTransport(idp), mailer=False)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def c(app):  # noqa: ANN001, ANN201
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://identity") as client:
        yield client


async def _owner(c: httpx.AsyncClient, app, email: str = "olga@acme.dev",  # noqa: ANN001
                 org: str = "Acme", verified: bool = True) -> dict:
    s = (await c.post("/v1/signup", json={"email": email, "password": PW, "org_name": org})).json()
    if verified:
        await app.state.store.mark_verified(s["user"]["user_id"])
    # single sign-on is the Business plan's
    await app.state.store.set_org_tier(s["org"]["org_id"], "business", "2999-01-01")
    return s


def _h(session: dict) -> dict:
    return {"Authorization": f"Bearer {session['access_token']}"}


SETUP = {"issuer": ISSUER, "client_id": "axor-client", "client_secret": "s3cret",
         "domains": ["acme.dev"], "default_role": "member"}


async def _sign_in(c: httpx.AsyncClient, idp: FakeIdP, email: str = "new@acme.dev",
                   code: str = "good") -> dict:
    start = await c.get("/v1/sso/start", params={"email": email, "return_to": "/#settings"})
    assert start.status_code == 302, start.text
    loc = start.headers["location"]
    if not loc.startswith(ISSUER):
        return parse_qs(urlsplit(loc).query)
    q = parse_qs(urlsplit(loc).query)
    assert q["redirect_uri"] == [f"{PUBLIC}/v1/sso/callback"]
    assert q["code_challenge_method"] == ["S256"] and q["login_hint"] == [email]
    idp.nonce = q["nonce"][0]
    back = await c.get("/v1/sso/callback", params={"state": q["state"][0], "code": code})
    assert back.status_code == 302, back.text
    return parse_qs(urlsplit(back.headers["location"]).query)


async def test_an_admin_sets_it_up_for_a_domain_they_proved(c, app) -> None:  # noqa: ANN001
    olga = await _owner(c, app, verified=False)
    org = olga["org"]["org_id"]
    r = await c.put(f"/v1/orgs/{org}/sso", json=SETUP, headers=_h(olga))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "domain_unproven"
    await app.state.store.mark_verified(olga["user"]["user_id"])
    gmail = await c.put(f"/v1/orgs/{org}/sso", json={**SETUP, "domains": ["gmail.com"]},
                        headers=_h(olga))
    assert gmail.status_code == 422
    r = await c.put(f"/v1/orgs/{org}/sso", json=SETUP, headers=_h(olga))
    assert r.status_code == 200, r.text
    got = (await c.get(f"/v1/orgs/{org}/sso", headers=_h(olga))).json()
    assert got["configured"] and got["has_secret"] and "client_secret" not in got
    assert got["callback_url"] == f"{PUBLIC}/v1/sso/callback"
    # another workspace cannot take the domain
    other = await _owner(c, app, email="eve@acme.dev", org="Evil")
    taken = await c.put(f"/v1/orgs/{other['org']['org_id']}/sso", json=SETUP, headers=_h(other))
    assert taken.status_code == 409
    # an issuer that is not an OpenID provider is refused before it is saved
    bad = await c.put(f"/v1/orgs/{org}/sso", json={**SETUP, "issuer": "https://nope.example"},
                      headers=_h(olga))
    assert bad.status_code == 422
    assert (await c.get("/v1/sso/discover", params={"email": "x@acme.dev"})).json() == {
        "sso": True, "workspace": "Acme", "enforce": False}
    nobody = await c.get("/v1/sso/discover", params={"email": "x@else.dev"})
    assert nobody.json() == {"sso": False}


async def test_a_work_address_signs_in_through_the_provider(c, app, idp) -> None:  # noqa: ANN001
    olga = await _owner(c, app)
    org = olga["org"]["org_id"]
    await c.put(f"/v1/orgs/{org}/sso", json=SETUP, headers=_h(olga))
    back = await _sign_in(c, idp)
    assert back["axor_org"] == [org] and "axor_login" in back
    assert idp.saw_auth.startswith("Basic ")        # the client authenticated
    session = (await c.post("/v1/oauth/exchange",
                            json={"code": back["axor_login"][0], "org_id": org})).json()
    assert session["created"] is True
    assert session["org"] == {"org_id": org, "role": "member", "tier": "business"}
    assert session["user"]["email"] == "new@acme.dev"
    # the same person again: the same account, no second one
    again = await _sign_in(c, idp)
    s2 = (await c.post("/v1/oauth/exchange", json={"code": again["axor_login"][0]})).json()
    assert s2["user"]["user_id"] == session["user"]["user_id"] and s2["created"] is False


async def test_an_existing_account_at_the_domain_joins(c, app, idp) -> None:  # noqa: ANN001
    olga = await _owner(c, app)
    org = olga["org"]["org_id"]
    await c.put(f"/v1/orgs/{org}/sso", json=SETUP, headers=_h(olga))
    # signed up on their own before the company set SSO up
    petr = await _owner(c, app, email="petr@acme.dev", org="Petr's", verified=False)
    idp.person = {"sub": "okta-002", "email": "petr@acme.dev", "email_verified": True}
    back = await _sign_in(c, idp, email="petr@acme.dev")
    s = (await c.post("/v1/oauth/exchange", json={"code": back["axor_login"][0],
                                                  "org_id": org})).json()
    assert s["user"]["user_id"] == petr["user"]["user_id"] and s["org"]["role"] == "member"


async def test_what_does_not_hold_is_refused(c, app, idp) -> None:  # noqa: ANN001
    olga = await _owner(c, app)
    await c.put(f"/v1/orgs/{olga['org']['org_id']}/sso", json=SETUP, headers=_h(olga))
    # an address the provider asserts at someone else's domain
    idp.person = {"sub": "okta-9", "email": "ceo@victim.dev", "email_verified": True}
    back = await _sign_in(c, idp)
    assert "not at a domain this workspace signs in" in back["axor_login_error"][0]
    # a token for another app
    idp.person = {"sub": "okta-1", "email": "new@acme.dev"}
    idp.aud = "someone-else"
    assert "does not hold" in (await _sign_in(c, idp))["axor_login_error"][0]
    idp.aud = "axor-client"
    # signed by a key the provider does not publish
    idp.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert "does not hold" in (await _sign_in(c, idp))["axor_login_error"][0]
    # a code the provider refuses
    idp.__init__()
    assert "refused" in (await _sign_in(c, idp, code="bad"))["axor_login_error"][0]
    # an address with no SSO: said so, not a dead end
    nope = await _sign_in(c, idp, email="x@else.dev")
    assert "no single sign-on" in nope["axor_login_error"][0]


async def test_a_replayed_token_with_another_nonce_is_refused(c, app, idp) -> None:  # noqa: ANN001
    olga = await _owner(c, app)
    await c.put(f"/v1/orgs/{olga['org']['org_id']}/sso", json=SETUP, headers=_h(olga))
    start = await c.get("/v1/sso/start", params={"email": "new@acme.dev"})
    q = parse_qs(urlsplit(start.headers["location"]).query)
    idp.nonce = "a-nonce-from-another-sign-in"
    back = await c.get("/v1/sso/callback", params={"state": q["state"][0], "code": "good"})
    assert "not issued for this sign-in" in parse_qs(
        urlsplit(back.headers["location"]).query)["axor_login_error"][0]


async def test_enforced_sso_keeps_its_people_off_passwords(c, app, idp) -> None:  # noqa: ANN001
    olga = await _owner(c, app)
    org = olga["org"]["org_id"]
    petr = await _owner(c, app, email="petr@acme.dev", org="Petr's")
    await c.put(f"/v1/orgs/{org}/sso", json={**SETUP, "enforce": True}, headers=_h(olga))
    r = await c.post("/v1/login", json={"email": "petr@acme.dev", "password": PW})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "sso_required"
    # the owner keeps the way back in, whatever the provider does
    assert (await c.post("/v1/login", json={"email": "olga@acme.dev", "password": PW,
                                            "org_id": org})).status_code == 200
    assert petr  # their account stays; they sign in through the provider
    idp.person = {"sub": "okta-002", "email": "petr@acme.dev", "email_verified": True}
    assert "axor_login" in await _sign_in(c, idp, email="petr@acme.dev")


async def test_only_an_admin_sets_it(c, app) -> None:  # noqa: ANN001
    olga = await _owner(c, app)
    org = olga["org"]["org_id"]
    assert (await c.get(f"/v1/orgs/{org}/sso")).status_code == 401
    other = await _owner(c, app, email="ann@other.dev", org="Other")
    assert (await c.put(f"/v1/orgs/{org}/sso", json=SETUP,
                        headers=_h(other))).status_code == 403


async def test_sso_is_the_business_plans(c, app) -> None:  # noqa: ANN001
    olga = await _owner(c, app)
    org = olga["org"]["org_id"]
    await app.state.store.set_org_tier(org, "coding", "2999-01-01")
    got = (await c.get(f"/v1/orgs/{org}/sso", headers=_h(olga))).json()
    assert got["plan_ok"] is False
    r = await c.put(f"/v1/orgs/{org}/sso", json=SETUP, headers=_h(olga))
    assert r.status_code == 402 and r.json()["detail"]["plan"] == "business"
    # a Business license past its date is the free plan again
    await app.state.store.set_org_tier(org, "business", "2000-01-01")
    assert (await c.put(f"/v1/orgs/{org}/sso", json=SETUP, headers=_h(olga))).status_code == 402


async def test_the_plan_comes_from_the_vendor_signed_license(tmp_path, idp) -> None:  # noqa: ANN001
    from axor_backend.ee.license import sign_license
    from nacl.signing import SigningKey

    vendor = SigningKey.generate()
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/lic.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={}, public_url=PUBLIC, mailer=False,
                             oauth_transport=httpx.MockTransport(idp),
                             vendor_pubkey=vendor.verify_key.encode().hex())
    async with application.router.lifespan_context(application), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://identity") as c:
        olga = (await c.post("/v1/signup", json={"email": "olga@acme.dev", "password": PW,
                                                 "org_name": "Acme"})).json()
        org = olga["org"]["org_id"]

        def lic(**over: object) -> str:
            return sign_license({"organization": org, "workspace_tier": "business",
                                 "governed_node_ceiling": 1, "self_hosted_runner": False,
                                 "expires_at": "2999-01-01", "features": [], **over},
                                bytes(vendor).hex())

        async def put(raw: str) -> int:
            return (await c.put(f"/v1/orgs/{org}/license", json={"license_json": raw},
                                headers=_h(olga))).status_code

        assert await put(lic(organization="org_x")) == 403        # someone else's
        assert await put(lic(expires_at="2001-01-01")) == 403     # past its date
        forged = json.loads(lic())
        forged["license"]["workspace_tier"] = "enterprise"
        assert (await c.put(f"/v1/orgs/{org}/license", json={"license_json": json.dumps(forged)},
                            headers=_h(olga))).status_code == 403
        r = await c.put(f"/v1/orgs/{org}/license", json={"license_json": lic()}, headers=_h(olga))
        assert r.status_code == 200 and r.json()["tier"] == "business"
        assert (await c.get(f"/v1/orgs/{org}/sso", headers=_h(olga))).json()["plan_ok"] is True


async def test_a_license_the_deployment_was_given_counts(tmp_path, idp, monkeypatch) -> None:  # noqa: ANN001
    """AXOR_LICENSE in the environment, pinned with AXOR_ORG, puts every
    workspace on a single-company install on its plan — nobody pastes it."""
    from axor_backend.ee.license import sign_license
    from nacl.signing import SigningKey

    vendor = SigningKey.generate()
    monkeypatch.setenv("AXOR_ORG", "Acme Inc")
    monkeypatch.setenv("AXOR_LICENSE", sign_license(
        {"organization": "Acme Inc", "workspace_tier": "business", "governed_node_ceiling": 1,
         "self_hosted_runner": True, "expires_at": "2999-01-01", "features": []},
        bytes(vendor).hex()))
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/env.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={}, public_url=PUBLIC, mailer=False,
                             oauth_transport=httpx.MockTransport(idp),
                             vendor_pubkey=vendor.verify_key.encode().hex())
    async with application.router.lifespan_context(application), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://identity") as c:
        olga = (await c.post("/v1/signup", json={"email": "olga@acme.dev", "password": PW,
                                                 "org_name": "Acme"})).json()
        org = olga["org"]["org_id"]
        assert (await c.get(f"/v1/orgs/{org}/sso", headers=_h(olga))).json()["plan_ok"] is True
    # a forged one counts for nothing
    monkeypatch.setenv("AXOR_LICENSE", '{"license": {}, "sig": "00"}')
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/env2.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={}, public_url=PUBLIC, mailer=False,
                             vendor_pubkey=vendor.verify_key.encode().hex())
    async with application.router.lifespan_context(application), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://identity") as c:
        olga = (await c.post("/v1/signup", json={"email": "olga@acme.dev", "password": PW,
                                                 "org_name": "Acme"})).json()
        org = olga["org"]["org_id"]
        assert (await c.get(f"/v1/orgs/{org}/sso", headers=_h(olga))).json()["plan_ok"] is False


async def test_a_renewed_license_moves_the_plan_forward_with_no_caller(tmp_path, idp) -> None:  # noqa: ANN001
    """The control plane renews on its own schedule and has no admin's session
    to paste the next license with. It hands it over with no caller at all —
    which is safe only because nothing but a vendor-signed license for this
    workspace, in date and ending later than the plan in force, is taken."""
    from axor_backend.ee.license import sign_license
    from nacl.signing import SigningKey

    vendor = SigningKey.generate()
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/renew.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={}, public_url=PUBLIC, mailer=False,
                             oauth_transport=httpx.MockTransport(idp),
                             vendor_pubkey=vendor.verify_key.encode().hex())
    async with application.router.lifespan_context(application), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://identity") as c:
        olga = (await c.post("/v1/signup", json={"email": "olga@acme.dev", "password": PW,
                                                 "org_name": "Acme"})).json()
        org = olga["org"]["org_id"]
        store = application.state.store
        await store.set_org_tier(org, "business", "2998-01-01")

        def lic(**over: object) -> str:
            return sign_license({"organization": org, "workspace_tier": "business",
                                 "governed_node_ceiling": 1, "self_hosted_runner": False,
                                 "expires_at": "2999-01-01", "features": [], **over},
                                bytes(vendor).hex())

        async def renew(raw: str, to: str = org) -> int:
            return (await c.post(f"/v1/orgs/{to}/license/renewal",
                                 json={"license_json": raw})).status_code

        async def plan() -> tuple[str, str]:
            held = await store.get_org(org)
            return held["tier"], held["tier_expires"]

        forged = json.loads(lic(workspace_tier="enterprise"))
        forged["license"]["workspace_tier"] = "security"
        assert await renew(json.dumps(forged)) == 403                 # not the vendor's
        assert await renew(lic(organization="org_x")) == 403          # someone else's
        assert await renew(lic(expires_at="2001-01-01")) == 403       # past its date
        assert await renew(lic(expires_at="2998-01-01")) == 409       # not later
        assert await renew(lic(expires_at="2997-01-01",
                               workspace_tier="coding")) == 409       # a replayed older one
        assert await renew(lic(), to="org_nobody") == 404
        assert await plan() == ("business", "2998-01-01")             # none of it moved it

        assert await renew(lic()) == 200
        assert await plan() == ("business", "2999-01-01")

        # QA-130: an upgrade bought mid-period is issued to the same end date —
        # it moves the plan up; the lower one, replayed at that date, does not
        assert await renew(lic(workspace_tier="team")) == 200
        assert await plan() == ("team", "2999-01-01")
        assert await renew(lic(workspace_tier="business")) == 409
        assert await renew(lic(workspace_tier="team")) == 409          # the same one again
        assert await plan() == ("team", "2999-01-01")


async def test_a_downgrade_in_the_same_period_moves_the_plan_down_once_the_plane_holds_it(
        tmp_path, idp, monkeypatch) -> None:  # noqa: ANN001
    """QA-305: a plan moved down mid-period is issued to the same end date. The
    file cannot tell that from an older license replayed, so identity takes it
    only when the control plane says it is the license in force there — and
    keeps refusing it when the plane holds another, or cannot be asked."""
    from axor_backend.ee.license import sign_license
    from nacl.signing import SigningKey

    vendor = SigningKey.generate()
    plane: dict = {"answer": None, "asked_with": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "plane.test":
            plane["asked_with"] = request.headers.get("authorization", "")
            if plane["answer"] is None:
                return httpx.Response(503)
            return httpx.Response(200, json=plane["answer"])
        return idp(request)

    monkeypatch.setenv("AXOR_IDENTITY_PLANE_URL", "http://plane.test")
    application = create_app(f"sqlite+aiosqlite:///{tmp_path}/down.db",
                             signing_key=load_signing_key(generate_pem(), kid="k"),
                             providers={}, public_url=PUBLIC, mailer=False,
                             oauth_transport=httpx.MockTransport(handler),
                             vendor_pubkey=vendor.verify_key.encode().hex())
    async with application.router.lifespan_context(application), httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url="http://identity") as c:
        olga = (await c.post("/v1/signup", json={"email": "olga@acme.dev", "password": PW,
                                                 "org_name": "Acme"})).json()
        org = olga["org"]["org_id"]
        store = application.state.store
        await store.set_org_tier(org, "team", "2999-01-01")

        def lic(tier: str) -> str:
            return sign_license({"organization": org, "workspace_tier": tier,
                                 "governed_node_ceiling": 1, "self_hosted_runner": False,
                                 "expires_at": "2999-01-01", "features": []},
                                bytes(vendor).hex())

        async def renew(raw: str) -> int:
            return (await c.post(f"/v1/orgs/{org}/license/renewal",
                                 json={"license_json": raw})).status_code

        async def tier() -> str:
            return (await store.get_org(org))["tier"]

        # the plane cannot be asked: refused, nothing moves
        assert await renew(lic("coding")) == 409
        assert await tier() == "team"
        # the plane holds another plan (team): a replayed coding file is refused
        plane["answer"] = {"organization": org, "workspace_tier": "team",
                           "expires_at": "2999-01-01"}
        assert await renew(lic("coding")) == 409
        assert await tier() == "team"
        assert plane["asked_with"].startswith("Bearer ")
        # the plane holds coding: identity follows it down
        plane["answer"] = {"organization": org, "workspace_tier": "coding",
                           "expires_at": "2999-01-01"}
        assert await renew(lic("coding")) == 200
        assert await tier() == "coding"
        # the same one again is no move at all
        assert await renew(lic("coding")) == 409
