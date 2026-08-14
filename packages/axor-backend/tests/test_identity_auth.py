"""Logging into the control-plane with an axor-identity access token.

With a JWKS configured, a request may authenticate as a human (kind="user")
instead of with an operator credential. The identity role maps to the scope
ladder — a viewer reads, an owner may mint keys — and the org rides along on
the principal for tenant scoping. Operator master token + API keys are
unchanged. Untrusted tokens are refused.
"""
from __future__ import annotations

import base64
import pathlib
import time

import httpx
import pytest
from axor_backend.app import create_app

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

TOKEN = "master-secret-xyz"
KID = "k1"


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
async def client(tmp_path: pathlib.Path, priv: Ed25519PrivateKey):  # noqa: ANN201
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/i.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN,
                     identity_jwks=_jwks(priv))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


def _mint(priv: Ed25519PrivateKey, **over: object) -> str:
    now = int(time.time())
    claims = {"iss": "axor-identity", "sub": "usr_1", "eml": "a@acme.io",
              "org": "org_acme", "role": "owner", "tier": "team",
              "iat": now, "exp": now + 900}
    claims.update(over)
    return jwt.encode(claims, priv, algorithm="EdDSA", headers={"kid": KID})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_identity_token_authenticates_a_read(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    r = await client.get("/v1/runs", headers=_bearer(_mint(priv)))
    assert r.status_code == 200


async def test_viewer_may_read_but_not_mint_keys(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    viewer = _mint(priv, role="viewer")
    assert (await client.get("/v1/runs", headers=_bearer(viewer))).status_code == 200
    # minting API keys is admin scope; a viewer role does not carry it
    denied = await client.post("/v1/keys", headers=_bearer(viewer),
                              json={"scopes": ["read"], "label": "x"})
    assert denied.status_code == 403


async def test_owner_role_carries_admin_scope(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    # owner passes the admin scope gate — the request reaches the handler
    # (whatever it then returns), it is not a 401/403
    r = await client.post("/v1/keys", headers=_bearer(_mint(priv)),
                         json={"scopes": ["read"], "label": "dash"})
    assert r.status_code not in (401, 403)


async def test_master_token_and_unknown_token(
        client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/runs", headers=_bearer(TOKEN))).status_code == 200
    assert (await client.get("/v1/runs", headers=_bearer("nope"))).status_code == 401


async def test_untrusted_tokens_are_refused(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    # tampered
    assert (await client.get("/v1/runs",
            headers=_bearer(_mint(priv)[:-4] + "AAAA"))).status_code == 401
    # wrong issuer
    assert (await client.get("/v1/runs",
            headers=_bearer(_mint(priv, iss="evil")))).status_code == 401
    # expired
    past = int(time.time()) - 3600
    assert (await client.get("/v1/runs",
            headers=_bearer(_mint(priv, iat=past, exp=past + 60)))).status_code == 401
    # signed by a different key
    other = Ed25519PrivateKey.generate()
    assert (await client.get("/v1/runs",
            headers=_bearer(_mint(other)))).status_code == 401
