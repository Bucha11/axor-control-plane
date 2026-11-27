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


async def test_a_workspace_makes_its_own_vault_token_and_then_needs_it(
        client: httpx.AsyncClient) -> None:
    """No deployment token on a hosted plane: each workspace mints its own per
    subsystem; from then on that subsystem asks for it, and the posture says so."""
    assert (await client.get("/v1/vault/creds/sealing-key")).status_code == 200
    r = await client.post("/v1/vault/tokens/creds")
    assert r.status_code == 201, r.text
    token, header = r.json()["token"], r.json()["header"]
    assert header == "X-Vault-Creds-Token" and token.startswith("axv_creds_")

    assert (await client.get("/v1/vault/creds/sealing-key")).status_code == 403
    assert (await client.get("/v1/vault/creds/sealing-key",
                             headers={header: "wrong"})).status_code == 403
    assert (await client.get("/v1/vault/creds/sealing-key",
                             headers={header: token})).status_code == 200
    # the other subsystem keeps its own wall: not opened, not closed by this one
    listed = (await client.get("/v1/vault/tokens")).json()
    assert listed["creds"]["set"] is True and listed["signing"]["set"] is False
    assert "hash" not in str(listed)   # never the token, nor its hash

    items = {i["id"]: i for i in (await client.get("/v1/security/posture")).json()["items"]}
    assert items["vault_creds_token"]["ok"] is True
    assert items["vault_signing_token"]["ok"] is False

    # a new token cuts off the old one
    newer = (await client.post("/v1/vault/tokens/creds")).json()["token"]
    assert (await client.get("/v1/vault/creds/sealing-key",
                             headers={header: token})).status_code == 403
    assert (await client.get("/v1/vault/creds/sealing-key",
                             headers={header: newer})).status_code == 200
    assert (await client.delete("/v1/vault/tokens/creds")).status_code == 200
    assert (await client.get("/v1/vault/creds/sealing-key")).status_code == 200
    assert (await client.post("/v1/vault/tokens/keys")).status_code == 404


async def test_a_deployment_token_rules_and_a_workspace_one_is_refused(
        tmp_path: pathlib.Path) -> None:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/d.db",
                     operator_keys={}, allow_unsigned=True, vault_creds_token="deploy")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://backend.test") as c, \
            app.router.lifespan_context(app):
        assert (await c.post("/v1/vault/tokens/creds")).status_code == 409
        assert (await c.get("/v1/vault/creds/sealing-key",
                            headers={"X-Vault-Creds-Token": "deploy"})).status_code == 200
