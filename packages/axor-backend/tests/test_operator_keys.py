"""Operators' browser keys: the plane keeps the public half, checks commands
against it, records the signature for the node, takes an endorsement only when
a trusted key signed it, and serves revocations."""
from __future__ import annotations

import base64
import pathlib
import time
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx
import jwt
import pytest
from axor_backend.app import create_app
from axor_backend.signing import signed_payload
from axor_core.kernel.jcs import canonicalize
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KID = "k1"


def _jwks(priv: Ed25519PrivateKey) -> dict:
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": x, "use": "sig",
                      "alg": "EdDSA", "kid": KID}]}


def _user(priv: Ed25519PrivateKey, email: str, role: str = "owner") -> dict:
    now = int(time.time())
    token = jwt.encode({"iss": "axor-identity", "sub": f"usr_{email}", "eml": email,
                        "org": "org_acme", "role": role, "tier": "community",
                        "iat": now, "exp": now + 900}, priv, algorithm="EdDSA",
                       headers={"kid": KID})
    return {"Authorization": f"Bearer {token}"}


def _pub(k: Ed25519PrivateKey) -> str:
    return k.public_key().public_bytes(serialization.Encoding.Raw,
                                       serialization.PublicFormat.Raw).hex()


@pytest.fixture
def idp() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
async def client(tmp_path: pathlib.Path,
                 idp: Ed25519PrivateKey) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/o.db",
                     operator_keys={}, allow_unsigned=True, api_token="m",
                     identity_jwks=_jwks(idp))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://cp.test") as c:
            yield c


async def _register(c: httpx.AsyncClient, h: dict, k: Ed25519PrivateKey) -> dict:
    r = await c.post("/v1/operators", headers=h, json={"pubkey": _pub(k), "label": "laptop"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_a_browser_key_signs_commands_and_the_signature_reaches_the_node(
        client: httpx.AsyncClient, idp: Ed25519PrivateKey) -> None:
    ada = _user(idp, "ada@acme.io")
    k = Ed25519PrivateKey.generate()
    op = (await _register(client, ada, k))["operator_id"]
    assert op == f"ada@acme.io#{_pub(k)[:8]}"
    node = "agent-1"
    # once a browser key exists, the plane asks commands for a signature
    r = await client.post(f"/v1/plane/{node}/command", headers=ada,
                          json={"version": 1, "state": {"paused": True}})
    assert r.status_code == 403
    ts = "2026-11-24T00:00:00Z"
    sig = k.sign(signed_payload(node, 1, {"paused": True}, ts)).hex()
    r = await client.post(f"/v1/plane/{node}/command", headers=ada, json={
        "version": 1, "state": {"paused": True}, "operator": op, "timestamp": ts, "sig": sig})
    assert r.status_code < 300, r.text
    # the connect command gets the key
    keys = (await client.get("/v1/coding/operator-keys", headers=ada)).json()["keys"]
    assert keys == {op: _pub(k)}


async def test_an_endorsement_must_verify_with_a_trusted_key_and_revocation_is_served(
        client: httpx.AsyncClient, idp: Ed25519PrivateKey) -> None:
    ada, sam = _user(idp, "ada@acme.io"), _user(idp, "sam@acme.io", role="admin")
    ka, ks = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    a = (await _register(client, ada, ka))["operator_id"]
    reg = await _register(client, sam, ks)
    s, doc = reg["operator_id"], reg["endorse"]
    assert doc == {"kind": "operator_key", "org": "org_acme", "operator": s,
                   "pubkey": _pub(ks)}
    # a key does not vouch for itself, and a stranger's signature is refused
    self_sig = {"payload": doc, "operator": s, "sig": ks.sign(canonicalize(doc)).hex()}
    assert (await client.put(f"/v1/operators/{quote(s, safe='')}/endorsement", headers=ada,
                             json=self_sig)).status_code == 400
    bad = {"payload": doc, "operator": a,
           "sig": Ed25519PrivateKey.generate().sign(canonicalize(doc)).hex()}
    assert (await client.put(f"/v1/operators/{quote(s, safe='')}/endorsement", headers=ada,
                             json=bad)).status_code == 403
    good = {"payload": doc, "operator": a, "sig": ka.sign(canonicalize(doc)).hex()}
    assert (await client.put(f"/v1/operators/{quote(s, safe='')}/endorsement", headers=ada,
                             json=good)).status_code == 200
    policy = (await client.get("/v1/coding/policy", headers=ada)).json()
    assert policy["operators"]["endorsed"] == [{"operator": s, "pubkey": _pub(ks),
                                                "endorsement": good}]
    # a member cannot revoke someone else's key; the owner can
    member = _user(idp, "bob@acme.io", role="member")
    url = f"/v1/operators/{quote(s, safe='')}"
    assert (await client.delete(url, headers=member)).status_code == 403
    assert (await client.delete(url, headers=ada)).status_code == 200
    policy = (await client.get("/v1/coding/policy", headers=ada)).json()
    assert policy["operators"] == {"endorsed": [], "revoked": [s]}
    assert s not in (await client.get("/v1/coding/operator-keys", headers=ada)).json()["keys"]


async def test_only_an_owner_or_admin_registers_and_only_a_real_key(
        client: httpx.AsyncClient, idp: Ed25519PrivateKey) -> None:
    member = _user(idp, "bob@acme.io", role="member")
    assert (await client.post("/v1/operators", headers=member,
                              json={"pubkey": "00" * 32})).status_code == 403
    ada = _user(idp, "ada@acme.io")
    assert (await client.post("/v1/operators", headers=ada,
                              json={"pubkey": "zz"})).status_code == 400
