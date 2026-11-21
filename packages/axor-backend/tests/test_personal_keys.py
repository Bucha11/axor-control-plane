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
    # what a member's role carries: an operator key — reports, reads, steers
    assert key["scopes"] == ["read", "ingest", "operate"]
    agent = _bearer(key["secret"])
    assert (await client.get("/v1/coding/policy", headers=agent)).status_code == 200
    assert (await client.get("/v1/runs", headers=agent)).status_code == 200
    # never admin: it mints no keys, for itself or anyone
    assert (await client.post("/v1/keys/mine", headers=agent)).status_code == 403
    minted = await client.post("/v1/keys", json={"scopes": ["read"]}, headers=agent)
    assert minted.status_code == 403
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


async def test_an_agent_key_is_steered_not_only_heard(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    """What the agent's own tools read on an ingest key: its desired state
    (pause / stop / cap), the plan (a mixed tree), the last health check."""
    from axor_backend.auth import Principal, required_scope

    agent_key = Principal(kind="key", key_id="k", scopes=frozenset({"ingest"}))
    viewer = Principal(kind="user", key_id="u", scopes=frozenset({"read"}))
    # the desired-state subscription is an endless SSE stream, so the decision
    # is checked here rather than by opening it
    need = required_scope("GET", "/v1/plane/claude-code-dev2-1/desired")
    assert agent_key.may(need) and viewer.may(need)
    owner = _bearer(_mint(priv, role="owner"))
    agent = _bearer((await client.post("/v1/keys", json={"scopes": ["ingest"]},
                                       headers=owner)).json()["secret"])
    assert (await client.get("/v1/license/entitlements", headers=agent)).status_code == 200
    assert (await client.get("/v1/setup-health/latest", headers=agent)).status_code in (200, 404)
    # ...and still nothing a person reads
    assert (await client.get("/v1/plane/nodes", headers=agent)).status_code == 403


async def test_a_node_bound_key_reads_only_its_own_desired_state(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    owner = _bearer(_mint(priv, role="owner"))
    key = (await client.post("/v1/keys", json={"scopes": ["ingest"], "node_id": "agent-a"},
                             headers=owner)).json()["secret"]
    async with client.stream("GET", "/v1/plane/agent-b/desired", headers=_bearer(key)) as r:
        assert r.status_code == 403


async def test_the_owner_changes_a_keys_role(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    member = _bearer(_mint(priv, role="member", sub="usr_dev2", eml="dev2@acme.io"))
    owner = _bearer(_mint(priv, role="owner"))
    made = (await client.post("/v1/keys", json={"scopes": ["ingest"]}, headers=owner)).json()
    agent = _bearer(made["secret"])
    assert (await client.get("/v1/runs", headers=agent)).status_code == 403
    # a member does not change a key's role
    up = {"scopes": ["read", "ingest"]}
    mine = await client.put(f"/v1/keys/{made['key_id']}", json=up, headers=member)
    assert mine.status_code == 403
    r = await client.put(f"/v1/keys/{made['key_id']}", json=up, headers=owner)
    assert r.status_code == 200 and r.json()["scopes"] == ["read", "ingest"]
    # the same secret, on the same machine, now reads the plane too
    assert (await client.get("/v1/runs", headers=agent)).status_code == 200
    listed = {k["key_id"]: k for k in (await client.get("/v1/keys", headers=owner)).json()}
    assert listed[made["key_id"]]["scopes"] == ["read", "ingest"]
    audit = (await client.get("/v1/keys/audit", headers=owner)).json()
    assert audit[0]["action"] == "change" and audit[0]["was"] == ["ingest"]
    assert (await client.put("/v1/keys/nope", json=up, headers=owner)).status_code == 404


async def test_a_removed_teammate_loses_their_keys(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: F811
    dev2 = _bearer(_mint(priv, role="member", sub="usr_dev2", eml="dev2@acme.io"))
    dev3 = _bearer(_mint(priv, role="member", sub="usr_dev3", eml="dev3@acme.io"))
    admin = _bearer(_mint(priv, role="admin", sub="usr_lee", eml="lee@acme.io"))
    k1 = (await client.post("/v1/keys/mine", headers=dev2)).json()
    k2 = (await client.post("/v1/keys/mine", headers=dev2)).json()
    kept = (await client.post("/v1/keys/mine", headers=dev3)).json()
    # a member — or a member's operator key — takes nobody's access away
    assert (await client.delete("/v1/keys/of/usr_dev2", headers=dev3)).status_code == 403
    assert (await client.delete("/v1/keys/of/usr_dev2",
                                headers=_bearer(kept["secret"]))).status_code == 403
    r = await client.delete("/v1/keys/of/usr_dev2", headers=admin)
    assert r.status_code == 200
    assert sorted(r.json()["revoked"]) == sorted([k1["key_id"], k2["key_id"]])
    gone = await client.get("/v1/coding/policy", headers=_bearer(k1["secret"]))
    assert gone.status_code == 401
    still = await client.get("/v1/coding/policy", headers=_bearer(kept["secret"]))
    assert still.status_code == 200
