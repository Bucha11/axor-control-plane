"""An access token identity has revoked since it was issued (QA-154, QA-240).

A removed teammate kept reading the workspace — fleet, billing, the workspace
itself — until their 15-minute access token expired, and a logged-out tab
stayed signed in just as long. The plane now reads identity's revocation feed
(cached for a few seconds) and refuses such a token, in words.
"""
from __future__ import annotations

import base64
import pathlib
import time

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.identity_client import RevocationFeed, member_key

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

KID = "k1"


def _jwks(priv: Ed25519PrivateKey) -> dict:
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": x, "kid": KID}]}


def _mint(priv: Ed25519PrivateKey, **over: object) -> str:
    now = int(time.time())
    claims = {"iss": "axor-identity", "sub": "usr_1", "eml": "a@acme.io",
              "org": "org_acme", "role": "member", "tier": "team",
              "iat": now, "exp": now + 900, "sid": "ses_a", "ep": 0}
    claims.update(over)
    return jwt.encode(claims, priv, algorithm="EdDSA", headers={"kid": KID})


class _Feed:
    def __init__(self) -> None:
        self.doc: dict = {"sessions": [], "members": []}
        self.calls = 0
        self.fail = False

    async def __call__(self, url: str) -> dict:
        self.calls += 1
        if self.fail:
            raise httpx.ConnectError("identity is down")
        return self.doc


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
async def plane(tmp_path: pathlib.Path, priv: Ed25519PrivateKey):  # noqa: ANN201
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/r.db",
                     operator_keys={}, allow_unsigned=True, api_token="master",
                     identity_jwks=_jwks(priv))
    feed, clock = _Feed(), _Clock()
    app.state.revocations = RevocationFeed("http://identity/v1/revocations",
                                           fetch=feed, clock=clock)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c, feed, clock


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_a_removed_member_is_refused_in_words(plane, priv) -> None:  # noqa: ANN001
    c, feed, clock = plane
    token = _mint(priv)
    assert (await c.get("/v1/workspace", headers=_h(token))).status_code == 200
    feed.doc = {"sessions": [], "members": [
        {"key": member_key("usr_1", "org_acme"), "epoch": 1, "reason": "removed"}]}
    clock.t += 10
    r = await c.get("/v1/workspace", headers=_h(token))
    assert r.status_code == 401
    assert r.json()["detail"] == "you are no longer a member of this workspace"
    # every read, not only the workspace
    assert (await c.get("/v1/plane/fleet", headers=_h(token))).status_code == 401
    # a token issued after re-joining carries the new epoch and reads again
    assert (await c.get("/v1/workspace", headers=_h(_mint(priv, ep=1)))).status_code == 200
    # someone else in the same workspace is not touched
    assert (await c.get("/v1/workspace",
                        headers=_h(_mint(priv, sub="usr_2", sid="ses_b")))).status_code == 200


async def test_a_role_downgrade_refuses_the_old_token(plane, priv) -> None:  # noqa: ANN001
    c, feed, clock = plane
    admin = _mint(priv, role="admin")
    feed.doc = {"sessions": [], "members": [
        {"key": member_key("usr_1", "org_acme"), "epoch": 1, "reason": "role"}]}
    r = await c.get("/v1/workspace", headers=_h(admin))
    assert r.status_code == 401 and "role in this workspace changed" in r.json()["detail"]
    viewer = _mint(priv, role="viewer", ep=1)
    assert (await c.get("/v1/workspace", headers=_h(viewer))).status_code == 200


async def test_a_signed_out_session_s_access_token_stops(plane, priv) -> None:  # noqa: ANN001
    c, feed, clock = plane
    token = _mint(priv)
    assert (await c.get("/v1/workspace", headers=_h(token))).status_code == 200
    feed.doc = {"sessions": ["ses_a"], "members": []}
    clock.t += 10
    r = await c.get("/v1/workspace", headers=_h(token))
    assert r.status_code == 401 and "signed out" in r.json()["detail"]
    # another session of the same person goes on
    assert (await c.get("/v1/workspace", headers=_h(_mint(priv, sid="ses_b")))).status_code == 200


async def test_the_feed_is_cached_and_identity_down_keeps_the_last_copy(
        plane, priv) -> None:  # noqa: ANN001
    c, feed, clock = plane
    feed.doc = {"sessions": ["ses_a"], "members": []}
    for _ in range(5):
        await c.get("/v1/workspace", headers=_h(_mint(priv)))
    assert feed.calls == 1  # at most one fetch per few seconds, not per request
    feed.fail = True
    clock.t += 10
    assert (await c.get("/v1/workspace", headers=_h(_mint(priv)))).status_code == 401
    assert (await c.get("/v1/workspace",
                        headers=_h(_mint(priv, sid="ses_b")))).status_code == 200


async def test_against_the_real_identity_service(tmp_path: pathlib.Path) -> None:
    """End to end: identity removes a member and logs a session out; the plane,
    reading identity's own feed, refuses both tokens on their next call."""
    identity_app = pytest.importorskip("axor_identity.app")
    from axor_identity.keys import generate_pem, load_signing_key

    key = load_signing_key(generate_pem(), kid="id")
    ident = identity_app.create_app(f"sqlite+aiosqlite:///{tmp_path}/id.db",
                                    signing_key=key, providers={}, mailer=False)
    plane_app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/cp.db",
                           operator_keys={}, allow_unsigned=True, api_token="master",
                           identity_jwks=key.jwks())

    async def fetch(url: str) -> dict:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ident),
                                     base_url="http://identity") as http:
            return (await http.get("/v1/revocations")).json()

    plane_app.state.revocations = RevocationFeed("http://identity/v1/revocations",
                                                 fetch=fetch, max_age=0)
    pw = "correct horse battery"
    async with ident.router.lifespan_context(ident), \
            plane_app.router.lifespan_context(plane_app), \
            httpx.AsyncClient(transport=httpx.ASGITransport(app=ident),
                              base_url="http://identity") as idc, \
            httpx.AsyncClient(transport=httpx.ASGITransport(app=plane_app),
                              base_url="http://cp.test") as cp:
        owner = (await idc.post("/v1/signup", json={"email": "o@acme.io", "password": pw,
                                                    "org_name": "Acme"})).json()
        org = owner["org"]["org_id"]
        invite = (await idc.post(f"/v1/orgs/{org}/invites", json={"role": "member"},
                                 headers=_h(owner["access_token"]))).json()["token"]
        member = (await idc.post("/v1/signup", json={"email": "m@acme.io", "password": pw,
                                                     "invite": invite})).json()
        assert (await cp.get("/v1/workspace",
                             headers=_h(member["access_token"]))).status_code == 200
        await idc.delete(f"/v1/orgs/{org}/members/{member['user']['user_id']}",
                         headers=_h(owner["access_token"]))
        r = await cp.get("/v1/workspace", headers=_h(member["access_token"]))
        assert r.status_code == 401
        assert r.json()["detail"] == "you are no longer a member of this workspace"
        # QA-240: logout ends the owner's access token on the plane too
        assert (await cp.get("/v1/workspace",
                             headers=_h(owner["access_token"]))).status_code == 200
        await idc.post("/v1/logout", json={"refresh_token": owner["refresh_token"]})
        r = await cp.get("/v1/workspace", headers=_h(owner["access_token"]))
        assert r.status_code == 401 and "signed out" in r.json()["detail"]
