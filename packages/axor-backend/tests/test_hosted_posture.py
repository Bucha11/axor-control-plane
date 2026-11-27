"""A multi-tenant (hosted) plane: walls per workspace, and no credential it can read.

Deployment-wide walls do not fit a plane with many tenants: one operator key
would sign for all of them, one vault token would be a secret they all share,
and AXOR_ORG pins a single organization. There a tool credential comes sealed
to the workspace's own key or not at all, and the posture says what holds.
"""
from __future__ import annotations

import base64
import pathlib

import httpx
import pytest
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> httpx.AsyncClient:
    monkeypatch.setenv("AXOR_HOSTED", "1")
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/h.db",
                     operator_keys={}, allow_unsigned=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://backend.test") as c:
        async with app.router.lifespan_context(app):
            yield c


async def test_a_plaintext_credential_is_refused_and_a_sealed_one_kept(
        client: httpx.AsyncClient) -> None:
    from nacl.public import PrivateKey, PublicKey, SealedBox

    body = {"tool": "t", "endpoint": "https://api.example", "scope_nodes": ["n"]}
    r = await client.post("/v1/vault/creds/enroll", json={**body, "secret": "s1"})
    assert r.status_code == 400
    assert "never stores a credential it can read" in r.json()["detail"]

    public = bytes(PrivateKey.generate().public_key).hex()
    assert (await client.post("/v1/vault/creds/sealing-key",
                              json={"public_key_hex": public})).status_code == 200
    sealed = base64.b64encode(
        SealedBox(PublicKey(bytes.fromhex(public))).encrypt(b"s1")).decode()
    r = await client.post("/v1/vault/creds/enroll", json={**body, "sealed_secret": sealed})
    assert r.status_code == 200, r.text
    r = await client.post("/v1/vault/creds/rotate", json={
        "tool": "t", "endpoint": "https://api.example", "secret": "s2"})
    assert r.status_code == 400


async def test_without_a_sealing_key_rotation_takes_no_plaintext_either(
        client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/vault/creds/rotate", json={
        "tool": "t", "endpoint": "https://api.example", "secret": "s2"})
    assert r.status_code == 400 and "never stores" in r.json()["detail"]


async def test_the_posture_says_what_holds_on_the_hosted_plane(
        client: httpx.AsyncClient) -> None:
    items = {i["id"]: i for i in (await client.get("/v1/security/posture")).json()["items"]}
    assert items["command_signing"]["ok"] is True
    assert "the workspace registers itself" in items["command_signing"]["what"]
    assert items["sealed_credentials"]["ok"] is True
    assert "licensed_org" not in items
    assert items["vault_creds_token"]["ok"] is False   # said, not hidden
    assert "AXOR_OPERATOR_KEYS" not in str(items)
