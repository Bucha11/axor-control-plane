"""EE org features (monetization Line 2): scheduled corpus CI + history and
notification routing. The gate is honest — everything safety-shaped stays
free; the 402 names the paid tier and never blocks a plain webhook or a
manual corpus run."""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.ee.license import sign_license
from axor_backend.notifications import Notifier


@pytest.fixture
def vendor(monkeypatch) -> dict:  # noqa: ANN001
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    pub = key.verify_key.encode().hex()
    monkeypatch.setenv("AXOR_VENDOR_PUBKEY", pub)
    # `control_plane: True` — these are Control Plane features. The fixture said
    # False and every test below still passed, because `modules` gated nothing:
    # `require_ee` takes a `module=` and no caller passed one.
    lic = sign_license(_license(), bytes(key).hex())
    return {"pub": pub, "priv": bytes(key).hex(), "license_json": lic}


def _license(**over: object) -> dict:
    base = {"organization": "T", "workspace_tier": "team",
            "modules": {"private_lab": True, "control_plane": True},
            "governed_node_ceiling": 10, "self_hosted_runner": False,
            "expires_at": "2999-01-01", "features": []}
    base.update(over)
    return base


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


async def test_community_tier_license_does_not_unlock_team_features(
    client: httpx.AsyncClient, vendor: dict,
) -> None:
    """Tier-aware gating (axor-packaging.md §1): a community-tier license is a
    valid, verified license but below the team tier — so it must NOT unlock the
    paid org features, and the 402 names the tier."""
    from axor_backend.ee.license import sign_license
    community = sign_license(
        {"organization": "T", "workspace_tier": "community",
         "modules": {"private_lab": True, "control_plane": False},
         "governed_node_ceiling": 0, "self_hosted_runner": False,
         "expires_at": "2999-01-01", "features": []},
        vendor["priv"],
    )
    r = await client.post("/v1/license/verify", json={"license_json": community})
    assert r.status_code == 200 and r.json()["activated"] is True
    hist = await client.get("/v1/regression/history")
    assert hist.status_code == 402
    assert "team" in hist.json()["detail"] and "community" in hist.json()["detail"]


async def test_license_activation_unlocks_and_persists(
    client: httpx.AsyncClient, vendor: dict,
) -> None:
    await _activate(client, vendor)
    status = (await client.get("/v1/license/status")).json()
    assert status["active"] is True and status["organization"] == "T"
    assert status["workspace_tier"] == "team"
    assert (await client.get("/v1/regression/history")).status_code == 200
    # The license landed in the settings KV — the boot rehydrate reads it.
    stored = await client._app.state.store.get_setting("license_json")  # type: ignore[attr-defined]
    assert json.loads(stored)["license"]["organization"] == "T"


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

    # One decision-cycle of the sweep loop, called directly — the real
    # function the loop calls per tenant, not a re-implementation of it.
    from axor_backend.lifecycle import run_due_schedule
    from axor_backend.tenancy import PUBLIC_ORG

    await run_due_schedule(app.state, PUBLIC_ORG)

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


# ── what a license has to answer, and each of these answered nothing ──────────

def _signed(vendor: dict, **over: object) -> str:
    return sign_license(_license(**over), vendor["priv"])


def _heartbeat(node_id: str) -> dict:
    """A node reporting in — what puts it in the fleet `list_nodes` counts."""
    return {"events": [{"schema_version": "1.0", "seq": 0, "node_id": node_id,
                        "kind": "heartbeat", "ts": "t", "causal_root": None,
                        "gate": None, "verdict": None,
                        "payload": {"applied_version": 0, "level": "NORMAL",
                                    "budget_remaining": None}}]}

class TestALicenseMustCoverTheModule:
    """`modules` is inside the signed payload and reported by /status, and it
    gated nothing: every `require_ee` call omitted `module=`, so a license
    carrying `control_plane: false` opened the Control Plane. The fixture above
    shipped exactly that license and every test still passed."""

    async def test_a_license_without_control_plane_does_not_unlock_it(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        lab_only = _signed(vendor, modules={"private_lab": True,
                                            "control_plane": False})
        assert (await client.post("/v1/license/verify",
                                  json={"license_json": lab_only})).status_code == 200
        r = await client.get("/v1/regression/history")
        assert r.status_code == 402
        assert "control_plane" in r.json()["detail"], r.json()

    async def test_the_same_license_with_the_module_unlocks_it(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        await _activate(client, vendor)
        assert (await client.get("/v1/regression/history")).status_code == 200


class TestALicenseMustBeIssuedToThisDeployment:
    """The signed payload names the organization the license was issued to, and
    nothing compared that name to anything — so one purchased file activated in
    any tenant of any deployment. The name is signed precisely so it can be
    checked."""

    @pytest.fixture
    async def bound(self, tmp_path: pathlib.Path, vendor: dict):  # noqa: ANN201
        app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/b.db",
                         org="T")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://backend.test",
        ) as c, app.router.lifespan_context(app):
            yield c

    async def test_a_license_issued_to_someone_else_is_refused(
        self, bound: httpx.AsyncClient, vendor: dict,
    ) -> None:
        r = await bound.post("/v1/license/verify",
                             json={"license_json": _signed(vendor, organization="globex")})
        assert r.status_code == 403
        assert "issued to 'globex'" in r.json()["detail"]
        assert (await bound.get("/v1/license/status")).json()["active"] is False

    async def test_our_own_license_activates(
        self, bound: httpx.AsyncClient, vendor: dict,
    ) -> None:
        r = await bound.post("/v1/license/verify",
                             json={"license_json": vendor["license_json"]})
        assert r.status_code == 200

    async def test_an_unbound_deployment_says_so(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """A single-tenant install that pins no AXOR_ORG accepts any license.
        That is a posture, not a secret: /status reports it, and boot warns —
        the same shape as AXOR_ALLOW_UNSIGNED."""
        status = (await client.get("/v1/license/status")).json()
        assert status["licensed_to"] is None
        r = await client.post("/v1/license/verify",
                              json={"license_json": _signed(vendor, organization="anyone")})
        assert r.status_code == 200


class TestAnExpiredLicenseIsNeverActivated:
    """Expiry was checked when a feature was USED, not when a license was
    STORED, so /verify answered 200 `activated: true` for a license that expired
    in 2020 and /status immediately said `active: false` — two answers to one
    question, in one sitting."""

    async def test_verify_refuses_it_and_stores_nothing(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        r = await client.post("/v1/license/verify",
                              json={"license_json": _signed(vendor, expires_at="2020-01-01")})
        assert r.status_code == 403
        assert "expired on 2020-01-01" in r.json()["detail"]
        assert (await client.get("/v1/license/status")).json()["active"] is False


class TestExpiryIsNotTheSameAsNeverHavingPaid:
    """A license that lapses while active produced the same 402 as no license at
    all — "add a license in Settings" — so a customer whose renewal slipped by a
    day read that they had never bought one. `active_license` knows the
    difference and was collapsing it."""

    async def test_the_402_names_the_expiry_date(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        from axor_backend.ee.license import verify_license
        from axor_backend.tenancy import PUBLIC_ORG

        await _activate(client, vendor)
        app = client._transport.app  # type: ignore[attr-defined]
        # a license that expired while the process was up — the renewal case,
        # which /verify cannot produce because it now refuses to store one
        app.state.licenses[PUBLIC_ORG] = verify_license(
            _signed(vendor, expires_at="2020-01-01"), vendor["pub"],
        )
        r = await client.get("/v1/regression/history")
        assert r.status_code == 402
        detail = r.json()["detail"]
        assert "expired on 2020-01-01" in detail, detail
        assert "add a license" not in detail, detail


class TestTheNodeCeilingIsWatchedNotEnforced:
    """`allows_nodes` existed and was called from nowhere; `over_ceiling` was
    computed once, at the moment a license was pasted, so a fleet that grew
    afterwards was never looked at again. Over-ceiling never refuses — a
    governed node is a safety surface, and safety never checks a license."""

    async def test_status_recomputes_it_against_the_live_fleet(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        assert (await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=1),
        })).json()["over_ceiling"] is False
        for node in ("n1", "n2", "n3"):
            await client.post(f"/v1/plane/{node}/telemetry", json=_heartbeat(node))
        status = (await client.get("/v1/license/status")).json()
        assert status["live_nodes"] == 3
        assert status["over_ceiling"] is True

    async def test_being_over_the_ceiling_refuses_nothing(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """Line 1: safety never checks a license. Neither governance nor the
        paid feature is switched off — the discrepancy is billing's."""
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=1)})
        for node in ("n1", "n2"):
            r = await client.post(f"/v1/plane/{node}/telemetry",
                                  json=_heartbeat(node))
            assert r.status_code == 202, r.text
        assert (await client.get("/v1/regression/history")).status_code == 200

    async def test_the_sweep_warns_about_it(
        self, client: httpx.AsyncClient, vendor: dict, caplog,  # noqa: ANN001
    ) -> None:
        import logging

        from axor_backend.lifecycle import warn_over_ceiling_once

        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=1)})
        for node in ("n1", "n2"):
            await client.post(f"/v1/plane/{node}/telemetry", json=_heartbeat(node))
        app = client._transport.app  # type: ignore[attr-defined]
        with caplog.at_level(logging.WARNING, logger="axor.backend"):
            await warn_over_ceiling_once(app.state)
        assert any("licensed ceiling" in r.getMessage()
                   for r in caplog.records), caplog.text
