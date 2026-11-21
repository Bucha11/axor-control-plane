"""A member connects their own machine: an ingest-only key, minted for themselves.

Minting keys is an owner's act, so every other developer on a team was
answered 403 by the Connect page's "create the key". `POST /v1/keys/mine`
lets any signed-in member mint a key that may only report — never read the
plane, never operate it — and list and revoke the ones they minted. The owner
still sees and may revoke every key.
"""
from __future__ import annotations

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from test_identity_auth import TOKEN, _bearer, _mint, client, priv  # noqa: F401

pytest.importorskip("jwt")


async def test_a_member_mints_a_key_for_their_own_machine(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    member = _bearer(_mint(priv, role="member", sub="usr_dev2", eml="dev2@acme.io"))
    # the owner-only route stays owner-only
    owner_only = await client.post("/v1/keys", json={"scopes": ["ingest"]}, headers=member)
    assert owner_only.status_code == 403
    r = await client.post("/v1/keys/mine", headers=member)
    assert r.status_code == 201, r.text
    key = r.json()
    assert key["scopes"] == ["ingest"]
    # it reports: the call a connected sidecar makes first
    agent = _bearer(key["secret"])
    assert (await client.get("/v1/coding/policy", headers=agent)).status_code == 200
    # it does not read the plane, and does not operate it
    assert (await client.get("/v1/runs", headers=agent)).status_code == 403
    assert (await client.post("/v1/keys/mine", headers=agent)).status_code == 403
    mine = (await client.get("/v1/keys/mine", headers=member)).json()
    assert [k["key_id"] for k in mine] == [key["key_id"]]
    assert mine[0]["label"].startswith("dev2@acme.io")


async def test_the_owner_sees_it_and_others_cannot_revoke_it(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    dev2 = _bearer(_mint(priv, role="member", sub="usr_dev2", eml="dev2@acme.io"))
    dev3 = _bearer(_mint(priv, role="member", sub="usr_dev3", eml="dev3@acme.io"))
    owner = _bearer(_mint(priv, role="owner"))
    key_id = (await client.post("/v1/keys/mine", headers=dev2)).json()["key_id"]
    assert key_id in {k["key_id"] for k in (await client.get("/v1/keys", headers=owner)).json()}
    assert (await client.get("/v1/keys/mine", headers=dev3)).json() == []
    assert (await client.delete(f"/v1/keys/mine/{key_id}", headers=dev3)).status_code == 404
    assert (await client.delete(f"/v1/keys/mine/{key_id}", headers=dev2)).status_code == 200
    assert key_id not in {k["key_id"] for k in (await client.get("/v1/keys", headers=owner)).json()}


async def test_a_viewer_cannot_mint_one(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    viewer = _bearer(_mint(priv, role="viewer"))
    assert (await client.post("/v1/keys/mine", headers=viewer)).status_code == 403
