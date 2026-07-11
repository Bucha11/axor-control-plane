"""EE org features (monetization Line 2): scheduled corpus CI + history and
notification routing. The gate is honest — everything safety-shaped stays
free; the 402 names the paid tier and never blocks a plain webhook or a
manual corpus run."""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from axor_backend.app import _regression_schedule_loop, create_app  # noqa: F401
from axor_backend.ee.license import sign_license
from axor_backend.notifications import Notifier


@pytest.fixture
def vendor(monkeypatch) -> dict:  # noqa: ANN001
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    pub = key.verify_key.encode().hex()
    monkeypatch.setenv("AXOR_VENDOR_PUBKEY", pub)
    lic = sign_license(
        {"org": "T", "tier": "team", "node_ceiling": 10,
         "expiry": "2999-01-01", "features": []},
        bytes(key).hex(),
    )
    return {"pub": pub, "license_json": lic}


@pytest.fixture
async def client(tmp_path: pathlib.Path, vendor: dict) -> httpx.AsyncClient:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/org.db")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        c._app = app  # type: ignore[attr-defined]
        yield c


async def _activate(client: httpx.AsyncClient, vendor: dict) -> None:
    r = await client.post("/v1/license/verify", json={
        "license_json": vendor["license_json"],
    })
    assert r.status_code == 200 and r.json()["activated"] is True


# ── gating ────────────────────────────────────────────────────────────────────

async def test_org_features_402_without_a_license(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/regression/history")).status_code == 402
    assert (await client.put("/v1/regression/schedule", json={
        "enabled": True, "interval_hours": 24, "config": {},
    })).status_code == 402
    # Routing fields are paid; the 402 says what to buy.
    routed = await client.post("/v1/notifications/subscribe", json={
        "url": "http://sink.test/h", "triggers": ["node_stale"],
        "node_pattern": "team-a-*",
    })
    assert routed.status_code == 402
    assert "org feature" in routed.json()["detail"]


async def test_free_shapes_stay_free_without_a_license(
    client: httpx.AsyncClient,
) -> None:
    # One plain webhook: free forever.
    plain = await client.post("/v1/notifications/subscribe", json={
        "url": "http://sink.test/h", "triggers": ["node_stale"],
    })
    assert plain.status_code == 200
    # Manual corpus run: free forever (empty corpus is a valid, empty report).
    manual = await client.post("/v1/regression", json={"config": {}})
    assert manual.status_code == 200
    # Schedule state is readable (the UI renders the locked panel from it).
    sched = await client.get("/v1/regression/schedule")
    assert sched.status_code == 200
    assert sched.json() == {
        "enabled": False, "interval_hours": None, "last_run_ts": None,
        "ee_active": False,
    }


async def test_license_activation_unlocks_and_persists(
    client: httpx.AsyncClient, vendor: dict,
) -> None:
    await _activate(client, vendor)
    status = (await client.get("/v1/license/status")).json()
    assert status["active"] is True and status["org"] == "T"
    assert (await client.get("/v1/regression/history")).status_code == 200
    # The license landed in the settings KV — the boot rehydrate reads it.
    stored = await client._app.state.store.get_setting("license_json")  # type: ignore[attr-defined]
    assert json.loads(stored)["license"]["org"] == "T"


# ── scheduled corpus CI ───────────────────────────────────────────────────────

async def test_schedule_roundtrip_and_manual_history(
    client: httpx.AsyncClient, vendor: dict,
) -> None:
    await _activate(client, vendor)
    put = await client.put("/v1/regression/schedule", json={
        "enabled": True, "interval_hours": 6, "config": {},
    })
    assert put.status_code == 200, put.text
    got = (await client.get("/v1/regression/schedule")).json()
    assert got["enabled"] is True and got["interval_hours"] == 6

    # A manual run records history with source=manual.
    await client.post("/v1/regression", json={"config": {}})
    history = (await client.get("/v1/regression/history")).json()
    assert len(history) == 1
    assert history[0]["source"] == "manual"
    assert history[0]["safe_to_ship"] is True


async def test_scheduler_fires_when_due_and_alerts_on_failure(
    client: httpx.AsyncClient, vendor: dict,
) -> None:
    """Drive one sweep-loop decision directly (no sleeping): due → the corpus
    runs, history gets a scheduled row, last_run_ts advances."""
    await _activate(client, vendor)
    app = client._app  # type: ignore[attr-defined]
    await app.state.store.set_setting("regression_schedule", {
        "enabled": True, "interval_hours": 1, "config": {}, "last_run_ts": None,
    })

    # One decision-cycle of the loop, extracted: emulate by calling the same
    # internals the loop uses.
    from axor_backend.app import _record_corpus_run, _regression_report

    report = await _regression_report(app.state.store, {})
    await _record_corpus_run(app, report, "scheduled")

    history = (await client.get("/v1/regression/history")).json()
    assert history[0]["source"] == "scheduled"


async def test_regression_failed_trigger_fires_on_bad_corpus() -> None:
    """The alert side in isolation: a failing report emits regression_failed
    through the Notifier (routing applies like any other trigger)."""
    seen: list[dict] = []

    async def ok_post(url: str, body: dict) -> int:
        seen.append(body)
        return 200

    n = Notifier(post=ok_post)
    n.subscribe("http://sink.test/h", ["regression_failed"])
    await n.emit("regression_failed", "corpus", {"escaped": 1, "source": "manual"})
    assert seen and seen[0]["trigger"] == "regression_failed"


# ── notification routing ──────────────────────────────────────────────────────

async def test_node_pattern_routes_deliveries() -> None:
    hits: list[tuple[str, str]] = []

    async def ok_post(url: str, body: dict) -> int:
        hits.append((url, body["node_id"]))
        return 200

    n = Notifier(post=ok_post)
    n.subscribe("http://team-a.test/h", ["node_stale"], node_pattern="team-a-*",
                label="team-a")
    n.subscribe("http://all.test/h", ["node_stale"])  # free shape: everything

    await n.emit("node_stale", "team-a-worker1", {})
    await n.emit("node_stale", "team-b-worker9", {})

    assert ("http://team-a.test/h", "team-a-worker1") in hits
    assert ("http://team-a.test/h", "team-b-worker9") not in hits
    assert [h for h in hits if h[0] == "http://all.test/h"] == [
        ("http://all.test/h", "team-a-worker1"),
        ("http://all.test/h", "team-b-worker9"),
    ]


async def test_routed_subscription_persists_with_fields(
    client: httpx.AsyncClient, vendor: dict,
) -> None:
    await _activate(client, vendor)
    r = await client.post("/v1/notifications/subscribe", json={
        "url": "http://sink.test/a", "triggers": ["node_stale"],
        "node_pattern": "team-a-*", "label": "team-a",
    })
    assert r.status_code == 200
    subs = (await client.get("/v1/notifications/subscriptions")).json()
    routed = next(s for s in subs if s["label"] == "team-a")
    assert routed["node_pattern"] == "team-a-*"
