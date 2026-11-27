"""Changing the plan of a workspace paid for by card (QA-131): an upgrade was a
new checkout and so a second subscription billing beside the first. The plane
asks the vendor's billing service to move the one subscription there is,
proving the workspace with the license it holds."""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.ee.license import sign_license


@pytest.fixture
def vendor(monkeypatch: pytest.MonkeyPatch) -> str:
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    monkeypatch.setenv("AXOR_VENDOR_PUBKEY", key.verify_key.encode().hex())
    return bytes(key).hex()


async def test_the_plan_change_goes_to_billing_with_the_license_held(
        tmp_path: pathlib.Path, vendor: str, monkeypatch: pytest.MonkeyPatch) -> None:
    held = sign_license({"organization": "public", "workspace_tier": "coding",
                         "governed_node_ceiling": 1, "self_hosted_runner": False,
                         "expires_at": "2999-01-01", "features": []}, vendor)
    seen: list[httpx.Request] = []

    def billing(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"from": "coding", "to": "team"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(billing), **kw))
    monkeypatch.setenv("AXOR_LICENSE_RENEWAL_URL", "https://billing.test/v1/renewal")
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db",
                     operator_keys={}, allow_unsigned=True)
    async with real(transport=httpx.ASGITransport(app=app), base_url="http://b.test") as c, \
            app.router.lifespan_context(app):
        assert (await c.post("/v1/license/plan", json={"tier": "team"})).status_code == 409
        await app.state.store.set_setting("license_json", held)
        r = await c.post("/v1/license/plan", json={"tier": "team"})
        assert r.status_code == 200, r.text
        assert r.json() == {"from": "coding", "to": "team"}
    (req,) = seen
    assert str(req.url) == "https://billing.test/v1/plan"
    assert json.loads(req.content) == {"license_json": held, "tier": "team"}
