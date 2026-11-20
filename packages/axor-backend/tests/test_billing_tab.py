"""Billing in one answer, for both journeys: the plan, this month so far, the
closed months — and on the free plan, what this month would cost on each paid
rung from the workspace's own usage."""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.ee.license import sign_license


async def _coding_dev(c: httpx.AsyncClient, node: str, dev: str) -> None:
    r = await c.post(f"/v1/plane/{node}/profile", json={
        "kind": "coding", "agent": "claude-code", "developer": dev})
    assert r.status_code < 300
    r = await c.post(f"/v1/plane/{node}/telemetry", json={"run_id": f"{node}-r", "events": []})
    assert r.status_code < 300, r.text


@pytest.fixture
async def open_client(tmp_path: pathlib.Path):  # noqa: ANN201
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/b.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def test_free_plan_shows_what_this_month_would_cost(open_client: httpx.AsyncClient) -> None:
    for i in range(3):
        await _coding_dev(open_client, f"cc-{i}", f"dev-{i}")
    body = (await open_client.get("/v1/license/billing")).json()
    assert body["plan"]["tier"] == "community" and body["plan"]["licensed"] is False
    assert body["current"] is None and body["history"] == []
    coding = next(e for e in body["if_paid"] if e["tier"] == "coding")
    assert coding["active_developers"] == 3 and coding["total"] == "$75.00"
    team = next(e for e in body["if_paid"] if e["tier"] == "team")
    assert team["total"] == "$299.00"       # five developers included


async def test_licensed_shows_this_month_and_the_closed_ones(
        tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    monkeypatch.setenv("AXOR_VENDOR_PUBKEY", key.verify_key.encode().hex())
    lic = sign_license({"organization": "Acme", "workspace_tier": "coding",
                        "governed_node_ceiling": 1, "self_hosted_runner": False,
                        "expires_at": "2999-01-01", "features": []}, bytes(key).hex())
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/l.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        assert (await c.post("/v1/license/verify", json={"license_json": lic})).status_code == 200
        await _coding_dev(c, "cc-a", "dev-a")
        await _coding_dev(c, "cc-b", "dev-b")
        body = (await c.get("/v1/license/billing")).json()
        assert body["plan"]["tier"] == "coding" and body["plan"]["seat"] == "$25.00"
        cur = body["current"]
        assert cur["provisional"] is True and cur["active_developers"] == 2
        assert cur["total"] == "$50.00" and len(cur["developers"]) == 2
        assert len(body["history"]) == 6 and all(not h["provisional"] for h in body["history"])
        assert body["if_paid"] == []


async def test_the_example_sessions_are_never_billed(open_client: httpx.AsyncClient) -> None:
    await open_client.post("/v1/demo/seed-coding")
    await _coding_dev(open_client, "cc-real", "dev-real")
    body = (await open_client.get("/v1/license/billing")).json()
    coding = next(e for e in body["if_paid"] if e["tier"] == "coding")
    assert coding["active_developers"] == 1
    assert [d["developer"] for d in coding["developers"]] == ["dev-real"]
