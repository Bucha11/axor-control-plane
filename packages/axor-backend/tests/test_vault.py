"""M6: Federation Vault (spec v2 Ch.5) — two secrets, two subsystems, one wall.

Tool creds: federation-scoped custody, DISPENSED, per-node scope checked at
dispense, fail-closed. Signing keys: custodied, SIGNED-not-surrendered,
per-request authorization, audited. Separate credentials to reach each.
"""
from __future__ import annotations

import ast
import base64
import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from nacl.signing import VerifyKey

CREDS_TOKEN = "creds-secret"
SIGNING_TOKEN = "signing-secret"


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={},
        allow_unsigned=True,
        vault_creds_token=CREDS_TOKEN,
        vault_signing_token=SIGNING_TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c:
        async with app.router.lifespan_context(app):
            yield c


CH = {"x-vault-creds-token": CREDS_TOKEN}
SH = {"x-vault-signing-token": SIGNING_TOKEN}


# ── tool credentials: dispensed, scoped, fail-closed ───────────────────────────


async def test_dispense_respects_per_node_scope(client: httpx.AsyncClient) -> None:
    await client.post("/v1/vault/creds/enroll", headers=CH, json={
        "tool": "payments", "endpoint": "https://pay.example",
        "secret": "sk_live_1", "scope_nodes": ["billing-agent"],
    })
    ok = await client.post("/v1/vault/creds/dispense", headers=CH, json={
        "node_id": "billing-agent", "tool": "payments",
        "endpoint": "https://pay.example",
    })
    assert ok.status_code == 200 and ok.json()["secret"] == "sk_live_1"

    # shared storage, partitioned access: the scraper cannot pull payments
    denied = await client.post("/v1/vault/creds/dispense", headers=CH, json={
        "node_id": "web-scraper", "tool": "payments",
        "endpoint": "https://pay.example",
    })
    assert denied.status_code == 403
    assert "scope mismatch" in denied.json()["detail"]


async def test_dispense_fails_closed_on_missing(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/vault/creds/dispense", headers=CH, json={
        "node_id": "n", "tool": "ghost", "endpoint": "https://x",
    })
    assert r.status_code == 403  # typed denial, never a silent empty secret


async def test_rotate_bumps_version_revoke_narrows(client: httpx.AsyncClient) -> None:
    await client.post("/v1/vault/creds/enroll", headers=CH, json={
        "tool": "search", "endpoint": "https://s", "secret": "v1",
        "scope_nodes": ["n"],
    })
    rot = (await client.post("/v1/vault/creds/rotate", headers=CH, json={
        "tool": "search", "endpoint": "https://s", "secret": "v2",
    })).json()
    assert rot["version"] == 2
    await client.post("/v1/vault/creds/revoke", headers=CH, json={
        "tool": "search", "endpoint": "https://s",
    })
    denied = await client.post("/v1/vault/creds/dispense", headers=CH, json={
        "node_id": "n", "tool": "search", "endpoint": "https://s",
    })
    assert denied.status_code == 403 and "revoked" in denied.json()["detail"]


# ── signing custody: sign, never surrender ─────────────────────────────────────


async def test_create_key_returns_public_half_only(client: httpx.AsyncClient) -> None:
    r = (await client.post("/v1/vault/signing/keys", headers=SH, json={
        "key_id": "fed-main", "operators": ["op_dmitrii"],
    })).json()
    assert "public_key_hex" in r
    assert "seed" not in str(r) and "seed_hex" not in r
    listed = (await client.get("/v1/vault/signing/keys", headers=SH)).json()
    assert all("seed_hex" not in k for k in listed)


async def test_sign_authorized_verifies_and_audits(client: httpx.AsyncClient) -> None:
    created = (await client.post("/v1/vault/signing/keys", headers=SH, json={
        "key_id": "fed-a", "operators": ["op_dmitrii"],
    })).json()
    payload = b"canonical-command-bytes"
    sig = (await client.post("/v1/vault/signing/sign", headers=SH, json={
        "key_id": "fed-a", "operator": "op_dmitrii",
        "payload_b64": base64.b64encode(payload).decode(),
    })).json()
    VerifyKey(bytes.fromhex(created["public_key_hex"])).verify(
        payload, bytes.fromhex(sig["signature_hex"])
    )  # raises on mismatch
    audit = (await client.get("/v1/vault/signing/audit", headers=SH)).json()
    assert audit[-1]["operator"] == "op_dmitrii" and audit[-1]["granted"] is True
    assert audit[-1]["payload_sha256"]


async def test_unauthorized_operator_refused_and_audited(
    client: httpx.AsyncClient,
) -> None:
    await client.post("/v1/vault/signing/keys", headers=SH, json={
        "key_id": "fed-b", "operators": ["op_dmitrii"],
    })
    r = await client.post("/v1/vault/signing/sign", headers=SH, json={
        "key_id": "fed-b", "operator": "op_mallory",
        "payload_b64": base64.b64encode(b"x").decode(),
    })
    assert r.status_code == 403
    audit = (await client.get("/v1/vault/signing/audit", headers=SH)).json()
    assert audit[-1] == {**audit[-1], "operator": "op_mallory", "granted": False}


# ── the wall ───────────────────────────────────────────────────────────────────


async def test_creds_token_cannot_reach_signing_and_vice_versa(
    client: httpx.AsyncClient,
) -> None:
    r = await client.post("/v1/vault/signing/sign",
                          headers={"x-vault-signing-token": CREDS_TOKEN},
                          json={"key_id": "k", "operator": "o", "payload_b64": ""})
    assert r.status_code == 403
    r = await client.post("/v1/vault/creds/dispense",
                          headers={"x-vault-creds-token": SIGNING_TOKEN},
                          json={"node_id": "n", "tool": "t", "endpoint": "e"})
    assert r.status_code == 403


def test_import_wall_between_the_two_subsystems() -> None:
    """Ch.5 §3, enforced: neither vault module imports the other. A single
    module spanning both would be the single-point-of-forgery the design
    exists to avoid."""
    src = pathlib.Path(__file__).parents[1] / "src" / "axor_backend"
    for a, b in (("vault_creds.py", "vault_signing"),
                 ("vault_signing.py", "vault_creds")):
        tree = ast.parse((src / a).read_text("utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(b in n.name for n in node.names), f"{a} imports {b}"
            if isinstance(node, ast.ImportFrom):
                assert b not in (node.module or ""), f"{a} imports {b}"
