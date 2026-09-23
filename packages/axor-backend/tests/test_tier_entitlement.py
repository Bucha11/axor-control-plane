"""On the vendor's hosted service the org's subscription tier — carried in the
identity login — entitles paid features exactly as a license of that tier
would. Everywhere else (`tier_entitles` off, the default) a tier claim proves
nothing: a self-hosted operator holds the identity signing key and could write
any tier, so the license stays the only entitlement there.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from fastapi import FastAPI

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)
from test_identity_auth import TOKEN, _bearer, _jwks, _mint  # noqa: E402

PAID = "/v1/regression/history"  # require_ee(..., min_tier="team")


async def _client(tmp_path: pathlib.Path, priv: Ed25519PrivateKey,
                  tier_entitles: bool) -> FastAPI:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN,
                     identity_jwks=_jwks(priv), tier_entitles=tier_entitles)
    return app


@pytest.fixture
def priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.mark.parametrize(("tier", "status"), [
    ("team", 200), ("security", 200), ("enterprise", 200),
    ("community", 402), ("platinum", 402),
])
async def test_hosted_tier_entitles_like_a_license(
        tmp_path: pathlib.Path, priv: Ed25519PrivateKey, tier: str,
        status: int) -> None:
    app = await _client(tmp_path, priv, tier_entitles=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        r = await c.get(PAID, headers=_bearer(_mint(priv, tier=tier)))
        assert r.status_code == status, r.text
        if status == 402:
            assert "Billing" in r.json()["detail"]


async def test_self_hosted_ignores_the_tier_claim(
        tmp_path: pathlib.Path, priv: Ed25519PrivateKey) -> None:
    app = await _client(tmp_path, priv, tier_entitles=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        r = await c.get(PAID, headers=_bearer(_mint(priv, tier="enterprise")))
        assert r.status_code == 402
        assert "LICENSE" in r.json()["detail"]


async def test_hosted_operator_credentials_carry_no_tier(
        tmp_path: pathlib.Path, priv: Ed25519PrivateKey) -> None:
    app = await _client(tmp_path, priv, tier_entitles=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        # the master token proves no plan: the license gate still applies
        r = await c.get(PAID, headers=_bearer(TOKEN))
        assert r.status_code == 402
