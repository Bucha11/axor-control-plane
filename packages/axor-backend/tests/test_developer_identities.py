"""A machine's developer id is bound to the person whose own key first reports
it: later sessions from that machine on a team key are theirs. A second person
claiming the id disputes it and it names nobody; an admin settles it."""
from __future__ import annotations

import base64
import pathlib
import time
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
from axor_backend.app import create_app
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

KID = "k1"


def _jwks(priv: Ed25519PrivateKey) -> dict:
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": x, "use": "sig",
                      "alg": "EdDSA", "kid": KID}]}


def _user(priv: Ed25519PrivateKey, email: str, role: str = "member") -> dict:
    now = int(time.time())
    token = jwt.encode({"iss": "axor-identity", "sub": f"usr_{email}", "eml": email,
                        "org": "org_acme", "role": role, "tier": "community",
                        "iat": now, "exp": now + 900}, priv, algorithm="EdDSA",
                       headers={"kid": KID})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
async def client(tmp_path: pathlib.Path,
                 priv: Ed25519PrivateKey) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/i.db",
                     operator_keys={}, allow_unsigned=True, api_token="m",
                     identity_jwks=_jwks(priv))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://cp.test") as c:
            yield c


async def _profile(c: httpx.AsyncClient, h: dict, node: str, dev: str) -> None:
    r = await c.post(f"/v1/plane/{node}/profile", headers=h,
                     json={"kind": "coding", "agent": "claude-code", "developer": dev})
    assert r.status_code < 300, r.text


async def _team_key(c: httpx.AsyncClient, admin: dict) -> dict:
    """A machine (team) key of the workspace: it names no person."""
    r = await c.post("/v1/keys", headers=admin, json={"scopes": ["ingest"]})
    assert r.status_code < 300, r.text
    return {"Authorization": f"Bearer {r.json()['secret']}"}


async def _developer(c: httpx.AsyncClient, h: dict, node: str) -> str:
    r = await c.get(f"/v1/coding/say/{node}/mode", headers=h)
    return "mine" if r.status_code == 200 else r.json()["detail"]["detail"]


async def test_a_machine_id_follows_its_person(client: httpx.AsyncClient,
                                                priv: Ed25519PrivateKey) -> None:
    ada, sam = _user(priv, "ada@acme.io"), _user(priv, "sam@acme.io")
    admin = _user(priv, "boss@acme.io", role="owner")
    # ada's own key reports a session from her laptop: the id is bound to her
    await _profile(client, ada, "cc-ada-1", "dev-laptop-ada")
    # a later session from that laptop arrives on a team key (no person): ada's
    await _profile(client, await _team_key(client, admin), "cc-ada-2", "dev-laptop-ada")
    ids = (await client.get("/v1/identities", headers=admin)).json()["identities"]
    assert [(i["developer_id"], i["email"]) for i in ids] == [("dev-laptop-ada", "ada@acme.io")]
    assert await _developer(client, ada, "cc-ada-2") == "mine"
    # not sam's
    assert "ada@acme.io" in await _developer(client, sam, "cc-ada-2")
    # only an owner or admin sees or changes the table
    assert (await client.get("/v1/identities", headers=sam)).status_code == 403


async def test_a_second_claim_disputes_and_an_admin_settles(client: httpx.AsyncClient,
                                                             priv: Ed25519PrivateKey) -> None:
    ada, sam = _user(priv, "ada@acme.io"), _user(priv, "sam@acme.io")
    admin = _user(priv, "boss@acme.io", role="owner")
    await _profile(client, ada, "cc-ada-1", "dev-shared-box")
    await _profile(client, sam, "cc-sam-1", "dev-shared-box")
    [row] = (await client.get("/v1/identities", headers=admin)).json()["identities"]
    assert row["disputed_by"] == "sam@acme.io"
    # a team-key session from that box names nobody now
    await _profile(client, await _team_key(client, admin), "cc-box-3", "dev-shared-box")
    kinds = (await client.get("/v1/coding/say/cc-box-3/mode", headers=sam))
    assert kinds.status_code == 200  # an opaque id: the team's, as before
    # the admin binds it to sam
    r = await client.put("/v1/identities/dev-shared-box", headers=admin,
                         json={"email": "sam@acme.io"})
    assert r.status_code == 200
    assert (await client.get("/v1/coding/say/cc-box-3/mode", headers=ada)).status_code == 403
    assert (await client.put("/v1/identities/dev-shared-box", headers=ada,
                             json={"email": "ada@acme.io"})).status_code == 403
    # unbinding gives it back to nobody
    await client.put("/v1/identities/dev-shared-box", headers=admin, json={"email": None})
    assert (await client.get("/v1/identities", headers=admin)).json()["identities"] == []
