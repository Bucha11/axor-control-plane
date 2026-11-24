"""What widens for a sidecar with operator keys comes from a policy an operator
signed: the plane serves the document, keeps the signature, refuses a stale or
older one, verifies it against its own keyring, and says when what stands is
no longer what was signed. Notes carry an operator's signature through."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.routers.coding_api import bundle_digest
from axor_core.kernel.jcs import canonicalize
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

H = {"Authorization": "Bearer master-signed"}


@pytest.fixture
def key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
async def client(tmp_path: pathlib.Path,
                 key: Ed25519PrivateKey) -> AsyncIterator[httpx.AsyncClient]:
    pub = key.public_key().public_bytes(serialization.Encoding.Raw,
                                        serialization.PublicFormat.Raw).hex()
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db",
                     operator_keys={"op_ui": pub}, allow_unsigned=False, api_token="master-signed")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://cp.test") as c:
            yield c


def _sign(key: Ed25519PrivateKey, payload: dict) -> dict:
    return {"payload": payload, "operator": "op_ui", "sig": key.sign(canonicalize(payload)).hex()}


def test_bundle_digest_vector_matches_the_sidecar() -> None:
    files = [{"path": "release/scripts/notes.sh", "content": "git log -5"},
             {"path": "release/SKILL.md", "content": "# Release\nrun make release"}]
    assert bundle_digest(files) == \
        "3e774240029dd52e0f0e74aecadd2f2f79dc1a3bbf12438bf0658a79852b2d6a"


async def test_sign_the_policy_and_see_when_it_goes_stale(client: httpx.AsyncClient,
                                                         key: Ed25519PrivateKey) -> None:
    r = await client.put("/v1/coding/rules", headers=H,
                         json={"allow": ["secrets"], "mode": "enforce"})
    assert r.status_code == 200
    doc = (await client.get("/v1/coding/policy/document", headers=H)).json()["payload"]
    assert doc["seq"] == 1 and doc["rules"]["allow"] == ["secrets"]
    # a bad signature is refused
    bad = _sign(Ed25519PrivateKey.generate(), doc)
    assert (await client.put("/v1/coding/policy/signature", headers=H, json=bad)).status_code == 403
    r = await client.put("/v1/coding/policy/signature", headers=H, json=_sign(key, doc))
    assert r.status_code == 200 and r.json() == {**r.json(), "seq": 1, "current": True}
    policy = (await client.get("/v1/coding/policy", headers=H)).json()
    assert policy["signed"]["payload"] == doc
    # replaying it is refused (not newer)
    assert (await client.put("/v1/coding/policy/signature", headers=H,
                             json=_sign(key, doc))).status_code == 409
    # the rules change: what stands is no longer what was signed
    await client.put("/v1/coding/rules", headers=H, json={"allow": [], "mode": "observe"})
    assert (await client.get("/v1/coding/rules", headers=H)).json()["signed"]["current"] is False
    # a document fetched before a change is refused
    stale = (await client.get("/v1/coding/policy/document", headers=H)).json()["payload"]
    await client.put("/v1/coding/rules", headers=H, json={"allow": ["secrets"], "mode": "observe"})
    assert (await client.put("/v1/coding/policy/signature", headers=H,
                             json=_sign(key, stale))).status_code == 409
    fresh = (await client.get("/v1/coding/policy/document", headers=H)).json()["payload"]
    assert fresh["seq"] == 2
    assert (await client.put("/v1/coding/policy/signature", headers=H,
                             json=_sign(key, fresh))).json()["current"] is True


async def test_a_signed_note_reaches_the_sidecar_with_its_signature(
        client: httpx.AsyncClient, key: Ed25519PrivateKey) -> None:
    node = "cc-ada-1"
    await client.post(f"/v1/plane/{node}/profile", headers=H,
                      json={"kind": "coding", "agent": "claude-code", "developer": "dev-ada"})
    text = "use the fake limiter"
    signed = _sign(key, {"kind": "note", "node_id": node, "text": text, "ts": "t"})
    assert (await client.post(f"/v1/coding/say/{node}", headers=H,
                              json={"text": text, "signed": signed})).status_code == 201
    # a signature over another text is refused
    assert (await client.post(f"/v1/coding/say/{node}", headers=H,
                              json={"text": "rm -rf /", "signed": signed})).status_code == 400
    [note] = (await client.get(f"/v1/coding/notes/{node}", headers=H)).json()["notes"]
    assert note["signed"] == signed
