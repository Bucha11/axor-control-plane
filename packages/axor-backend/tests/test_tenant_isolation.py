"""Per-org data isolation: one org's runs are invisible to another org and to
the operator (public) principal, and vice versa.

The store is scoped by the request principal's org (tenancy.py). A run ingested
under org A must not appear in org B's listing, org B's event stream, or the
operator master token's (public) listing.
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
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN,
                     identity_jwks=_jwks(priv))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


def _tok(priv: Ed25519PrivateKey, org: str, role: str = "owner") -> str:
    now = int(time.time())
    return jwt.encode(
        {"iss": "axor-identity", "sub": f"u_{org}", "eml": f"a@{org}.io", "org": org,
         "role": role, "tier": "team", "iat": now, "exp": now + 900},
        priv, algorithm="EdDSA", headers={"kid": KID})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _ingest(client: httpx.AsyncClient, token: str, run_id: str) -> None:
    r = await client.post(f"/v1/ingest/{run_id}", headers=_bearer(token),
                         json={"node_id": "n1", "scenario": "custom",
                               "events": [{"seq": 0, "kind": "decision",
                                           "node_id": "n1"}]})
    assert r.status_code == 202, r.text


async def _run_ids(client: httpx.AsyncClient, token: str) -> set[str]:
    r = await client.get("/v1/runs", headers=_bearer(token))
    assert r.status_code == 200, r.text
    runs = r.json()
    rows = runs if isinstance(runs, list) else runs.get("runs", runs)
    return {row["run_id"] for row in rows}


async def test_a_run_is_visible_only_within_its_org(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    org_a, org_b = _tok(priv, "org_a"), _tok(priv, "org_b")
    await _ingest(client, org_a, "run_a")

    assert "run_a" in await _run_ids(client, org_a)      # its own org sees it
    assert "run_a" not in await _run_ids(client, org_b)  # another org does not
    assert "run_a" not in await _run_ids(client, TOKEN)  # operator (public) does not


async def test_events_do_not_cross_orgs(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    org_a, org_b = _tok(priv, "org_a"), _tok(priv, "org_b")
    await _ingest(client, org_a, "run_x")
    # org B cannot read org A's run events
    a_events = await client.get("/v1/runs/run_x/events", headers=_bearer(org_a))
    b_events = await client.get("/v1/runs/run_x/events", headers=_bearer(org_b))
    assert a_events.json() and not b_events.json()


async def test_public_and_org_data_are_separate(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    # ingest under the operator (public) principal and under an org; neither sees
    # the other's run
    await _ingest(client, TOKEN, "run_pub")
    await _ingest(client, _tok(priv, "org_a"), "run_a")
    assert await _run_ids(client, TOKEN) == {"run_pub"}
    assert await _run_ids(client, _tok(priv, "org_a")) == {"run_a"}
