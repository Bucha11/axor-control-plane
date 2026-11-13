"""Guest sessions: a hosted visitor works before signing up, and keeps it.

The hosted plane answered every anonymous request with 401 while its home page
offered "no account". A guest gets a short-lived token and an org of its own,
works in it like a tenant, meets a sign-up exactly where a feature needs an
account, and signing up claims the guest org's work into the account.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import httpx
import pytest
from axor_backend.app import create_app

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

import base64  # noqa: E402
import time  # noqa: E402

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

TOKEN = "master-secret-guests"
KID = "k1"


def _jwks(priv: Ed25519PrivateKey) -> dict:
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": x, "use": "sig",
                      "alg": "EdDSA", "kid": KID}]}


def _mint(priv: Ed25519PrivateKey, **over: object) -> str:
    now = int(time.time())
    claims = {"iss": "axor-identity", "sub": "usr_1", "eml": "a@acme.io",
              "org": "org_acme", "role": "owner", "tier": "community",
              "iat": now, "exp": now + 900}
    claims.update(over)
    return jwt.encode(claims, priv, algorithm="EdDSA", headers={"kid": KID})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


async def _client(tmp_path: pathlib.Path, priv: Ed25519PrivateKey, monkeypatch, guests: bool):  # noqa: ANN001, ANN202
    if guests:
        monkeypatch.setenv("AXOR_GUEST_SESSIONS", "1")
    else:
        monkeypatch.delenv("AXOR_GUEST_SESSIONS", raising=False)
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/g.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN,
                     identity_jwks=_jwks(priv))
    return app


@pytest.fixture
async def client(tmp_path: pathlib.Path, priv: Ed25519PrivateKey, monkeypatch):  # noqa: ANN001, ANN201
    app = await _client(tmp_path, priv, monkeypatch, guests=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, app.router.lifespan_context(app):
        yield c


def _line(seq: int, kind: str, payload: dict, node: str) -> dict:
    return {"schema_version": "1.0", "seq": seq, "node_id": node, "kind": kind,
            "ts": datetime.now(UTC).isoformat(), "causal_root": None, "gate": None,
            "verdict": None, "payload": payload}


async def test_off_unless_turned_on(tmp_path, priv, monkeypatch) -> None:  # noqa: ANN001
    app = await _client(tmp_path, priv, monkeypatch, guests=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, app.router.lifespan_context(app):
        assert (await c.post("/v1/auth/guest")).status_code == 404
        assert (await c.get("/v1/auth/status")).json()["guest_sessions"] is False


async def test_a_guest_works_in_an_org_of_its_own(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/runs")).status_code == 401          # anonymous: still nothing
    status = (await client.get("/v1/auth/status")).json()
    assert status["guest_sessions"] is True
    g1 = (await client.post("/v1/auth/guest")).json()
    g2 = (await client.post("/v1/auth/guest")).json()
    assert g1["token"].startswith("gs_") and g1["org"] != g2["org"]
    h1, h2 = _bearer(g1["token"]), _bearer(g2["token"])
    assert (await client.get("/v1/auth/status", headers=h1)).json()["guest"] is True

    # a guest seeds the example fleet and operates it — in its own org
    assert (await client.post("/v1/demo/seed-fleet", headers=h1)).status_code in (200, 201)
    mine = {n["node_id"] for n in (await client.get("/v1/plane/nodes", headers=h1)).json()}
    theirs = {n["node_id"] for n in (await client.get("/v1/plane/nodes", headers=h2)).json()}
    assert mine and not theirs
    first = sorted(mine)[0]
    r = await client.post(f"/v1/plane/{first}/command", headers=h1,
                          json={"version": 1, "state": {"paused": True}})
    assert r.status_code == 202


async def test_account_features_ask_for_a_sign_up(client: httpx.AsyncClient) -> None:
    g = _bearer((await client.post("/v1/auth/guest")).json()["token"])
    for method, path in (("POST", "/v1/keys"), ("GET", "/v1/notifications"),
                         ("POST", "/v1/pins"), ("GET", "/v1/vault/creds")):
        r = await client.request(method, path, headers=g, json={})
        assert r.status_code == 403, path
        assert r.json()["error"] == "account_required"
        assert r.json()["feature"]


async def test_signing_up_claims_the_guest_work(client: httpx.AsyncClient, priv) -> None:  # noqa: ANN001
    guest = (await client.post("/v1/auth/guest")).json()
    g = _bearer(guest["token"])
    await client.post("/v1/plane/my-agent/telemetry", headers=g, json={
        "run_id": "my-agent-r1", "events": [
        _line(0, "tool_call", {"tool": "fetch", "tool_call_id": "a"}, "my-agent"),
        _line(1, "heartbeat", {"applied_version": 0, "level": "NORMAL"}, "my-agent"),
        ]})
    user = _bearer(_mint(priv, org="org_new", sub="usr_new"))
    assert (await client.get("/v1/plane/my-agent/runs", headers=user)).json()["runs"] == []

    claim = {"guest_token": guest["token"]}
    r = await client.post("/v1/auth/claim-guest", headers=user, json=claim)
    assert r.status_code == 200 and r.json()["claimed"] is True
    assert r.json()["moved"]["runs"] == 1
    runs = (await client.get("/v1/plane/my-agent/runs", headers=user)).json()["runs"]
    assert [x["run_id"] for x in runs] == ["my-agent-r1"]
    # the guest token is spent
    assert (await client.get("/v1/runs", headers=g)).status_code == 401
    # and claiming twice moves nothing
    again = await client.post("/v1/auth/claim-guest", headers=user, json=claim)
    assert again.json()["claimed"] is False


async def test_the_account_wins_a_clash(client: httpx.AsyncClient, priv) -> None:  # noqa: ANN001
    user = _bearer(_mint(priv, org="org_clash", sub="usr_c"))
    await client.post("/v1/plane/shared/command", headers=user,
                      json={"version": 1, "state": {"paused": True}})
    guest = (await client.post("/v1/auth/guest")).json()
    await client.post("/v1/plane/shared/command", headers=_bearer(guest["token"]),
                      json={"version": 1, "state": {"stopped": True}})
    await client.post("/v1/auth/claim-guest", headers=user, json={"guest_token": guest["token"]})
    nodes = {n["node_id"]: n for n in (await client.get("/v1/plane/nodes", headers=user)).json()}
    assert nodes["shared"]["desired"]["state"] == {"paused": True}


async def test_only_a_signed_in_user_claims(client: httpx.AsyncClient) -> None:
    guest = (await client.post("/v1/auth/guest")).json()
    r = await client.post("/v1/auth/claim-guest", headers=_bearer(TOKEN),
                          json={"guest_token": guest["token"]})
    assert r.status_code == 403
