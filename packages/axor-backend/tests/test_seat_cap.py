"""A workspace's limit on developer seats, and where this month's bill is
heading. The first ``cap`` developers to report in the month hold the seats; one
past it is protected, but not shown, operated or billed — and is told why."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator
from datetime import date, timedelta

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.ee.license import sign_license


async def _dev(c: httpx.AsyncClient, node: str, dev: str) -> dict:
    r = await c.post(f"/v1/plane/{node}/profile", json={
        "kind": "coding", "agent": "claude-code", "developer": dev})
    assert r.status_code < 300
    await c.post(f"/v1/plane/{node}/telemetry", json={"run_id": f"{node}-r", "events": []})
    return r.json()


@pytest.fixture
async def paid(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
               ) -> AsyncIterator[tuple[httpx.AsyncClient, object]]:
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    monkeypatch.setenv("AXOR_VENDOR_PUBKEY", key.verify_key.encode().hex())
    lic = sign_license({"organization": "Acme", "workspace_tier": "coding",
                        "governed_node_ceiling": 1, "self_hosted_runner": False,
                        "expires_at": "2999-01-01", "features": []}, bytes(key).hex())
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        assert (await c.post("/v1/license/verify", json={"license_json": lic})).status_code == 200
        yield c, app


async def test_past_the_limit_a_developer_is_kept_out_and_not_billed(paid) -> None:  # noqa: ANN001
    c, _ = paid
    await _dev(c, "cc-a", "dev-a")
    assert (await c.put("/v1/license/seat-cap", json={"cap": 2})).json()["seat_cap"] == 2
    assert (await _dev(c, "cc-b", "dev-b"))["locked"] == []
    third = await _dev(c, "cc-c", "dev-c")
    # said at once, so the sidecar tells the developer in their own agent
    assert third["locked"] == ["seat_cap"] and "seat limit" in third["detail"]
    ent = (await c.get("/v1/license/entitlements")).json()
    assert ent["seat_cap"] == 2 and ent["seats_used"] == 2
    assert ent["locked"] == {"cc-c": ["seat_cap"]}
    # a second session of a seated developer is not past the limit
    assert (await _dev(c, "cc-a2", "dev-a"))["locked"] == []
    cur = (await c.get("/v1/license/billing")).json()["current"]
    assert cur["active_developers"] == 2 and cur["over_cap"] == 1 and cur["total"] == "$50.00"
    assert [d["seated"] for d in cur["developers"]] == [True, True, False]
    # not operated either
    r = await c.post("/v1/plane/cc-c/command", json={"version": 1, "state": {"paused": True}})
    assert r.status_code == 402 and r.json()["detail"]["reasons"] == ["seat_cap"]
    # raised: the third holds a seat, and is billed
    await c.put("/v1/license/seat-cap", json={"cap": 3})
    assert (await c.get("/v1/license/entitlements")).json()["locked"] == {}
    assert (await c.get("/v1/license/billing")).json()["current"]["total"] == "$75.00"


async def test_the_limit_cannot_unbill_work_already_done(paid) -> None:  # noqa: ANN001
    c, _ = paid
    for d in ("a", "b", "c"):
        await _dev(c, f"cc-{d}", f"dev-{d}")
    low = await c.put("/v1/license/seat-cap", json={"cap": 2})
    assert low.status_code == 409 and low.json()["detail"]["used"] == 3
    assert (await c.put("/v1/license/seat-cap", json={"cap": 0})).status_code == 400
    assert (await c.put("/v1/license/seat-cap", json={"cap": "5"})).status_code == 400
    assert (await c.put("/v1/license/seat-cap", json={"cap": 3})).status_code == 200
    assert (await c.put("/v1/license/seat-cap", json={"cap": None})).json() == {"seat_cap": None}
    assert (await c.get("/v1/license/entitlements")).json()["seat_cap"] is None


async def test_the_forecast_counts_who_ran_agents_lately(paid) -> None:  # noqa: ANN001
    c, app = paid
    await _dev(c, "cc-a", "dev-a")
    # dev-b ran agents at the end of last month, not yet this one
    await c.post("/v1/plane/cc-b/profile", json={
        "kind": "coding", "agent": "codex", "developer": "dev-b"})
    first = date.today().replace(day=1)
    await app.state.store.record_node_activity("cc-b", (first - timedelta(days=1)).isoformat())
    body = (await c.get("/v1/license/billing")).json()
    f = body["forecast"]
    assert f["developers_so_far"] == 1 and body["current"]["total"] == "$25.00"
    if (date.today() - first).days < 29:     # last month's last day is within 30 days
        assert [x["developer"] for x in f["likely"]] == ["dev-b"]
        assert f["projected_developers"] == 2 and f["projected_total"] == "$50.00"
    # a limit holds the forecast too
    await c.put("/v1/license/seat-cap", json={"cap": 1})
    f = (await c.get("/v1/license/billing")).json()["forecast"]
    assert f["projected_developers"] == 1 and f["seats_left"] == 0


async def test_the_free_plan_has_no_seat_limit(tmp_path: pathlib.Path) -> None:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/f.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        assert (await c.put("/v1/license/seat-cap", json={"cap": 3})).status_code == 400
        assert (await c.get("/v1/license/billing")).json()["forecast"] is None
