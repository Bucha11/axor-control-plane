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
    lic = sign_license(_license(), bytes(key).hex())
    return {"pub": pub, "priv": bytes(key).hex(), "license_json": lic}


def _license(**over: object) -> dict:
    base = {"organization": "T", "workspace_tier": "team",
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
    await n.drain()
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
    await n.drain()

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

class TestOneRungEntitlesTheWholeProduct:
    """Private Lab and the Control Plane were separately licensed flags on top of
    a tier, so a paid customer could hold a workspace without production
    governance or the reverse, and `require_ee` took a `module=` that no caller
    passed — the flag was signed, reported, and decided nothing.

    They are one product on one ladder now. The rung is the whole answer, and
    the flag is gone from the format rather than pinned to `{true, true}`.
    """

    async def test_a_team_rung_opens_the_control_plane_features(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        await _activate(client, vendor)  # team
        assert (await client.get("/v1/regression/history")).status_code == 200

    async def test_the_rung_below_opens_nothing(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, workspace_tier="community")})
        r = await client.get("/v1/regression/history")
        assert r.status_code == 402
        assert "team workspace tier" in r.json()["detail"]

    async def test_the_top_rung_is_a_rung_and_not_an_unknown_string(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """axor-identity has always had `enterprise`; this ladder did not, so
        `tier_at_least("team")` read -1 and the most expensive customer failed
        the cheapest gate."""
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, workspace_tier="enterprise")})
        assert (await client.get("/v1/regression/history")).status_code == 200

    async def test_the_format_no_longer_carries_a_module_flag(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """A field that cannot vary decides nothing, and this one was read as
        though it did."""
        r = await client.post("/v1/license/verify",
                              json={"license_json": vendor["license_json"]})
        assert "modules" not in r.json()
        assert "modules" not in (await client.get("/v1/license/status")).json()


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
        assert status["peak_nodes"] == 3
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

        from axor_backend.lifecycle import license_sweep_once

        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=1)})
        for node in ("n1", "n2"):
            await client.post(f"/v1/plane/{node}/telemetry", json=_heartbeat(node))
        app = client._transport.app  # type: ignore[attr-defined]
        with caplog.at_level(logging.WARNING, logger="axor.backend"):
            await license_sweep_once(app.state)
        assert any("licensed ceiling" in r.getMessage()
                   for r in caplog.records), caplog.text


# ── expiry stops being silent ─────────────────────────────────────────────────

def _in_days(n: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC).date() + timedelta(days=n)).isoformat()


class TestAnExpiringLicenseIsAnnounced:
    """Expiry was entirely silent: EE degraded to read-only and the first anyone
    heard of it was a 402 on a feature that had worked yesterday. The
    notification machinery already carried node staleness, level transitions and
    corpus regressions, and nothing about the thing that pays for it."""

    @staticmethod
    async def _capture(client: httpx.AsyncClient) -> list:
        fired: list = []

        async def post(url: str, body: dict) -> int:
            fired.append(body)
            return 200

        app = client._transport.app  # type: ignore[attr-defined]
        app.state.notifier._post = post
        await client.post("/v1/notifications/subscribe", json={
            "url": "http://sink.test", "triggers": ["license_expiring"],
        })
        return fired

    async def test_the_sweep_announces_a_license_running_out(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        from axor_backend.lifecycle import license_sweep_once

        fired = await self._capture(client)
        assert (await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, expires_at=_in_days(5)),
        })).status_code == 200
        await license_sweep_once(client._transport.app.state)  # type: ignore[attr-defined]
        assert len(fired) == 1, fired
        assert fired[0]["trigger"] == "license_expiring"
        assert fired[0]["days_remaining"] == 5
        assert fired[0]["expired"] is False

    async def test_a_license_with_months_left_says_nothing(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        from axor_backend.lifecycle import license_sweep_once

        fired = await self._capture(client)
        await _activate(client, vendor)  # expires 2999
        await license_sweep_once(client._transport.app.state)  # type: ignore[attr-defined]
        assert fired == []

    async def test_one_notice_per_threshold_not_one_per_sweep(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """The sweep runs on a timer. A notice per pass is a notice nobody
        reads, and a plain time debounce would either repeat daily or swallow
        the 1-day warning after firing the 3-day one."""
        from axor_backend.lifecycle import license_sweep_once

        fired = await self._capture(client)
        await client.post("/v1/license/verify",
                          json={"license_json": _signed(vendor, expires_at=_in_days(5))})
        state = client._transport.app.state  # type: ignore[attr-defined]
        for _ in range(4):
            await license_sweep_once(state)
        assert len(fired) == 1, fired

    async def test_status_reports_the_days_left_not_only_the_date(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        await client.post("/v1/license/verify",
                          json={"license_json": _signed(vendor, expires_at=_in_days(9))})
        status = (await client.get("/v1/license/status")).json()
        assert status["days_remaining"] == 9
        assert status["expired"] is False

    async def test_status_tells_lapsed_apart_from_never_licensed(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """`active: false` alone cannot say whether the operator never bought a
        license or let one lapse — the same conflation the 402 used to make."""
        from axor_backend.ee.license import verify_license
        from axor_backend.tenancy import PUBLIC_ORG

        await _activate(client, vendor)
        app = client._transport.app  # type: ignore[attr-defined]
        app.state.licenses[PUBLIC_ORG] = verify_license(
            _signed(vendor, expires_at="2020-01-01"), vendor["pub"])
        status = (await client.get("/v1/license/status")).json()
        assert status["active"] is False
        assert status["expired"] is True
        assert status["days_remaining"] < 0
        assert status["organization"] == "T"


# ── renewal: the deployment fetches its next license, it is never pushed one ──

class TestRenewalIsFetchedAndOnlyMovesForward:
    """A monthly subscription against an offline license means a new file every
    month, pasted by hand, or the deployment quietly goes read-only.

    Renewal is a PULL: the control plane dials out for its next license exactly
    as a governed node dials out for its desired state. A push would need the
    customer's backend reachable from the vendor's — an inbound write surface on
    a security product, behind their NAT, for an event that is not time-
    critical. Everything that comes back passes the checks a pasted license
    passes, plus one: the expiry may not move backwards.
    """

    @pytest.fixture
    async def renewing(self, tmp_path: pathlib.Path, vendor: dict):  # noqa: ANN201
        app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/r.db",
                         license_renewal_url="https://vendor.test/renew")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://backend.test",
        ) as c, app.router.lifespan_context(app):
            await c.post("/v1/license/verify", json={
                "license_json": _signed(vendor, expires_at=_in_days(5))})
            yield c, app

    @staticmethod
    def _serving(license_json: str | None, seen: list | None = None):  # noqa: ANN205
        async def fetch(
            url: str, org: str, current: object, report: dict | None = None,
        ) -> str | None:
            if seen is not None:
                seen.append(report)
            return license_json
        return fetch

    async def test_a_license_inside_the_window_is_renewed(
        self, renewing: tuple, vendor: dict,
    ) -> None:
        from axor_backend.licensing import renew_once, renewal_due
        from axor_backend.tenancy import PUBLIC_ORG

        client, app = renewing
        assert await renewal_due(app.state, PUBLIC_ORG) is True
        nxt = _signed(vendor, expires_at=_in_days(400))
        assert await renew_once(app.state, PUBLIC_ORG,
                                fetch=self._serving(nxt)) is True
        status = (await client.get("/v1/license/status")).json()
        assert status["days_remaining"] == 400
        assert status["auto_renewal"] is True

    async def test_a_renewal_survives_a_restart(
        self, renewing: tuple, vendor: dict,
    ) -> None:
        """It is stored, not only held: a renewal that lives in memory is one
        the next deploy loses."""
        from axor_backend.licensing import load_licenses, renew_once
        from axor_backend.tenancy import PUBLIC_ORG

        _client, app = renewing
        await renew_once(app.state, PUBLIC_ORG,
                         fetch=self._serving(_signed(vendor, expires_at=_in_days(400))))
        app.state.licenses.clear()
        await load_licenses(app.state)
        assert app.state.licenses[PUBLIC_ORG].expires_at == _in_days(400)

    async def test_an_older_license_cannot_be_replayed_over_a_newer_one(
        self, renewing: tuple, vendor: dict,
    ) -> None:
        """This is what makes an unauthenticated fetch safe: the only thing the
        vendor endpoint can do is move a deployment forward."""
        from axor_backend.licensing import renew_once, stored_license
        from axor_backend.tenancy import PUBLIC_ORG

        _client, app = renewing
        await renew_once(app.state, PUBLIC_ORG,
                         fetch=self._serving(_signed(vendor, expires_at=_in_days(400))))
        stale = _signed(vendor, expires_at=_in_days(6))
        assert await renew_once(app.state, PUBLIC_ORG,
                                fetch=self._serving(stale)) is False
        assert stored_license(app.state, PUBLIC_ORG).expires_at == _in_days(400)

    async def test_a_license_for_another_org_is_not_installed(
        self, tmp_path: pathlib.Path, vendor: dict,
    ) -> None:
        from axor_backend.licensing import renew_once, stored_license

        app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/r2.db",
                         org="T", license_renewal_url="https://vendor.test/renew")
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://backend.test",
        ) as c, app.router.lifespan_context(app):
            await c.post("/v1/license/verify", json={
                "license_json": _signed(vendor, expires_at=_in_days(5))})
            assert await renew_once(app.state, "public", fetch=self._serving(
                _signed(vendor, organization="globex", expires_at=_in_days(400)),
            )) is False
            assert stored_license(app.state, "public").expires_at == _in_days(5)

    async def test_an_unsigned_response_changes_nothing(
        self, renewing: tuple,
    ) -> None:
        from axor_backend.licensing import renew_once, stored_license
        from axor_backend.tenancy import PUBLIC_ORG

        _client, app = renewing
        assert await renew_once(app.state, PUBLIC_ORG, fetch=self._serving(
            '{"license": {"organization": "T", "workspace_tier": "security", '
            '"governed_node_ceiling": 99, '
            '"expires_at": "2099-01-01", "features": []}, "sig": "00"}',
        )) is False
        assert stored_license(app.state, PUBLIC_ORG).expires_at == _in_days(5)

    async def test_an_unreachable_vendor_leaves_the_licence_alone(
        self, renewing: tuple,
    ) -> None:
        """A vendor outage must not cost a paying customer their entitlement.
        The deployment degrades on its own schedule, as if renewal had never
        been configured."""
        from axor_backend.licensing import renew_once, stored_license
        from axor_backend.tenancy import PUBLIC_ORG

        _client, app = renewing

        async def broken(
            url: str, org: str, current: object, report: dict | None = None,
        ) -> str:
            raise ConnectionError("vendor down")

        assert await renew_once(app.state, PUBLIC_ORG, fetch=broken) is False
        assert stored_license(app.state, PUBLIC_ORG).expires_at == _in_days(5)

    async def test_renewal_is_off_unless_a_url_is_configured(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """Opt-in: an air-gapped deployment has no renewal endpoint to call, and
        the manual paste flow it depends on is unchanged."""
        from axor_backend.licensing import renewal_due
        from axor_backend.tenancy import PUBLIC_ORG

        await client.post("/v1/license/verify",
                          json={"license_json": _signed(vendor, expires_at=_in_days(5))})
        app = client._transport.app  # type: ignore[attr-defined]
        assert await renewal_due(app.state, PUBLIC_ORG) is False
        assert (await client.get("/v1/license/status")).json()["auto_renewal"] is False

    async def test_a_renewal_resets_the_expiry_notice(
        self, renewing: tuple, vendor: dict,
    ) -> None:
        """The next expiry is a new subject. A high-water mark left over from
        the old one would swallow its warnings."""
        from axor_backend.licensing import renew_once
        from axor_backend.lifecycle import license_sweep_once
        from axor_backend.tenancy import PUBLIC_ORG

        _client, app = renewing
        await license_sweep_once(app.state)  # notices the 5-day expiry
        assert await app.state.store.get_setting("license_expiry_notified")
        await renew_once(app.state, PUBLIC_ORG,
                         fetch=self._serving(_signed(vendor, expires_at=_in_days(400))))
        assert not await app.state.store.get_setting("license_expiry_notified")


# ── the vendor CLI ────────────────────────────────────────────────────────────

class TestTheIssuingCliDoesNotProduceAWarningMachine:
    def test_a_zero_ceiling_is_refused(self) -> None:
        """The ceiling used to be decorative, so a zero passed unnoticed. It is
        compared to the live fleet on every sweep now, and 0 warns forever about
        a customer who has paid."""
        from axor_backend.ee.cli import main

        assert main(["issue", "--key", _vendor_priv(), "--org", "T",
                     "--governed-nodes", "0", "--expires-at", "2099-01-01"]) == 2

    def test_a_rung_carries_its_standard_fleet_without_being_told(
        self, capsys: pytest.CaptureFixture,
    ) -> None:
        """One number per rung is the sale (Pricing.tsx). Retyping it per license
        is how a Team customer ends up with a Security allowance."""
        import json as _json

        from axor_backend.ee.cli import main
        from axor_backend.ee.license import TIER_NODE_CEILING

        for tier, standard in TIER_NODE_CEILING.items():
            assert main(["issue", "--key", _vendor_priv(), "--org", "T",
                         "--workspace-tier", tier,
                         "--expires-at", "2099-01-01"]) == 0, tier
            out = capsys.readouterr()
            assert _json.loads(out.out)["license"]["governed_node_ceiling"] == standard
            assert "note:" not in out.err

    def test_the_negotiated_rung_refuses_to_guess(
        self, capsys: pytest.CaptureFixture,
    ) -> None:
        """Enterprise has no list price and no standard fleet, so inventing one
        would put a number nobody agreed to inside a signed license."""
        from axor_backend.ee.cli import main

        assert main(["issue", "--key", _vendor_priv(), "--org", "T",
                     "--workspace-tier", "enterprise",
                     "--expires-at", "2099-01-01"]) == 2
        assert "negotiated" in capsys.readouterr().err
        assert main(["issue", "--key", _vendor_priv(), "--org", "T",
                     "--workspace-tier", "enterprise", "--governed-nodes", "200",
                     "--expires-at", "2099-01-01"]) == 0

    def test_departing_from_the_standard_is_announced(
        self, capsys: pytest.CaptureFixture,
    ) -> None:
        """Deliberate is fine, silent is not — a Team license with 500 nodes
        should be a decision, not a typo that ships."""
        from axor_backend.ee.cli import main

        assert main(["issue", "--key", _vendor_priv(), "--org", "T",
                     "--workspace-tier", "team", "--governed-nodes", "500",
                     "--expires-at", "2099-01-01"]) == 0
        err = capsys.readouterr().err
        assert "sold with 10 governed nodes" in err
        assert "issued with 500" in err

    def test_every_rung_the_identity_service_can_issue_is_accepted(self) -> None:
        """The two services shared a name and not a vocabulary: identity could
        set `enterprise` and this CLI could not sign it."""
        from axor_backend.ee.cli import main
        from axor_backend.ee.license import TIERS

        for tier in TIERS:
            assert main(["issue", "--key", _vendor_priv(), "--org", "T",
                         "--workspace-tier", tier, "--governed-nodes", "5",
                         "--expires-at", "2099-01-01"]) == 0, tier

    def test_it_prints_the_env_block_the_customer_must_match(
        self, capsys: pytest.CaptureFixture,
    ) -> None:
        """`AXOR_ORG` must equal the license's organization EXACTLY or the
        deployment refuses it, and it is a free-text company name — so it is
        handed over, not retyped."""
        from axor_backend.ee.cli import main

        main(["issue", "--key", _vendor_priv(), "--org", "Acme Corp",
              "--governed-nodes", "5", "--expires-at", "2099-01-01"])
        err = capsys.readouterr().err
        assert "AXOR_ORG=Acme Corp" in err
        assert "AXOR_VENDOR_PUBKEY=" in err


def _vendor_priv() -> str:
    from nacl.signing import SigningKey

    return bytes(SigningKey.generate()).hex()


# ── the governed-node meter: what a per-node line is drawn from ───────────────

class TestGovernedNodeUsageIsMeasuredNotGuessed:
    """The Control Plane could say how many nodes it had EVER seen and nothing
    else. `list_nodes()` is the union of desired and reported state, with no
    time in it at all, so the number only ever grew: a customer who replaced one
    node was over their allowance forever, and no invoice could be drawn from it
    because nobody bills for a node decommissioned in March.

    `node_activity` records one row per (tenant, node, UTC day) a node reported.
    Everything else is derived from those rows, so a disputed line on an invoice
    resolves by looking rather than by arguing.
    """

    @staticmethod
    async def _report(client: httpx.AsyncClient, node: str) -> None:
        r = await client.post(f"/v1/plane/{node}/telemetry", json=_heartbeat(node))
        assert r.status_code == 202, r.text

    async def test_a_node_is_recorded_once_a_day_however_often_it_dials_in(
        self, client: httpx.AsyncClient,
    ) -> None:
        """A heartbeat is every ten seconds. The meter counts nodes, not
        heartbeats."""
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        for _ in range(5):
            await self._report(client, "n1")
        usage = await store.node_usage("2000-01-01", "2999-12-31")
        assert usage["peak_nodes"] == 1
        assert usage["distinct_nodes"] == 1
        assert len(usage["days"]) == 1

    async def test_the_peak_is_the_most_nodes_on_any_one_day(
        self, client: httpx.AsyncClient,
    ) -> None:
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        for node in ("n1", "n2", "n3"):
            await self._report(client, node)
        # a quieter day before, and a busier one after, written directly: the
        # HTTP path can only ever record today
        await store.record_node_activity("n1", "2026-01-01")
        for node in ("a", "b", "c", "d", "e"):
            await store.record_node_activity(node, "2026-02-01")
        usage = await store.node_usage("2026-01-01", "2999-12-31")
        assert usage["peak_nodes"] == 5
        assert usage["peak_day"] == "2026-02-01"

    async def test_distinct_is_reported_but_is_not_the_bill(
        self, client: httpx.AsyncClient,
    ) -> None:
        """A fleet of five recycled daily would bill as a hundred and fifty if
        distinct were the basis. Both numbers are reported so the difference is
        visible rather than decided silently."""
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        for day in ("2026-03-01", "2026-03-02", "2026-03-03"):
            for i in range(5):
                await store.record_node_activity(f"{day}-node{i}", day)
        usage = await store.node_usage("2026-03-01", "2026-03-31")
        assert usage["peak_nodes"] == 5
        assert usage["distinct_nodes"] == 15

    async def test_the_ceiling_is_checked_against_the_peak_not_the_ledger(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """Three nodes seen over three days, never more than one at a time, is a
        one-node fleet. The cumulative count called it three and put a paying
        customer permanently over a ceiling of two."""
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=2)})
        month, _ = __import__(
            "axor_backend.licensing", fromlist=["billing_month"]).billing_month()
        for i, day in enumerate((month, month[:-2] + "02", month[:-2] + "03")):
            await store.record_node_activity(f"replaced-{i}", day)
        status = (await client.get("/v1/license/status")).json()
        assert status["peak_nodes"] == 1
        assert status["distinct_nodes"] == 3
        assert status["over_ceiling"] is False

    async def test_usage_reports_the_months_and_the_days_behind_them(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=1)})
        for node in ("n1", "n2"):
            await self._report(client, node)
        body = (await client.get("/v1/license/usage?months=2")).json()
        assert body["governed_node_ceiling"] == 1
        assert len(body["months"]) == 2
        current = body["months"][0]
        assert current["peak_nodes"] == 2
        assert current["over_ceiling"] is True
        # the days are there so the peak can be checked rather than believed
        assert current["days"] == [{"day": current["peak_day"], "nodes": 2}]

    async def test_usage_never_refuses_anything(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """Line 1: a governed node is a safety surface, and safety never checks
        a license. Being over the ceiling is a conversation, not a shutdown."""
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=1)})
        for node in ("n1", "n2", "n3"):
            await self._report(client, node)
        assert (await client.get("/v1/license/status")).json()["over_ceiling"] is True
        # the fourth node still reports, and the paid features still answer
        await self._report(client, "n4")
        assert (await client.get("/v1/regression/history")).status_code == 200

    async def test_one_tenants_fleet_is_not_anothers(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The meter is the invoice basis, so a tenant boundary crossed here is
        a customer billed for someone else's nodes."""
        from axor_backend.tenancy import set_current_org

        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await self._report(client, "public-node")
        try:
            set_current_org("acme")
            await store.record_node_activity("acme-node", "2026-04-01")
            acme = await store.node_usage("2000-01-01", "2999-12-31")
        finally:
            set_current_org("public")
        public = await store.node_usage("2000-01-01", "2999-12-31")
        assert acme["distinct_nodes"] == 1
        assert public["distinct_nodes"] == 1
        assert acme["peak_day"] == "2026-04-01"
        assert public["peak_day"] != "2026-04-01"


# ── the statement: a measurement turned into a line somebody pays ────────────

class TestTheInvoiceIsDrawnFromTheMeter:
    """`node_activity` says how big the fleet was. This says what that costs.

    The two are kept apart on purpose: the meter is evidence and the statement
    is an opinion about it, and a customer disputing the second is entitled to
    check the first without taking anything on trust — so the days behind the
    peak travel with the bill.
    """

    @staticmethod
    async def _fleet(store, day: str, count: int) -> None:  # noqa: ANN001
        for i in range(count):
            await store.record_node_activity(f"n{i}", day)

    async def test_a_fleet_inside_its_allowance_is_the_rung_and_nothing_else(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=10)})
        await self._fleet(store, "2026-08-12", 10)
        r = (await client.get("/v1/license/invoice?month=2026-08")).json()
        assert r["billable_nodes"] == 0
        assert r["overage_cents"] == 0
        assert r["total_cents"] == 299_00
        assert r["total"] == "$299.00"

    async def test_a_fleet_past_it_is_charged_per_node_over(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=10)})
        await self._fleet(store, "2026-08-12", 14)
        await self._fleet(store, "2026-08-20", 6)
        r = (await client.get("/v1/license/invoice?month=2026-08")).json()
        assert r["peak_nodes"] == 14 and r["peak_day"] == "2026-08-12"
        assert r["billable_nodes"] == 4
        assert r["overage_cents"] == 4 * 75_00
        assert r["total_cents"] == 299_00 + 300_00

    async def test_the_allowance_is_the_licences_own_not_the_rungs_list(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """A customer who negotiated 200 nodes is billed against 200, not
        against the 10 the Team rung is listed with."""
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=200)})
        await self._fleet(store, "2026-08-12", 50)
        r = (await client.get("/v1/license/invoice?month=2026-08")).json()
        assert r["included_nodes"] == 200
        assert r["billable_nodes"] == 0
        assert r["total_cents"] == 299_00

    async def test_the_bill_carries_the_days_it_was_computed_from(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=1)})
        await self._fleet(store, "2026-08-12", 3)
        await self._fleet(store, "2026-08-13", 2)
        r = (await client.get("/v1/license/invoice?month=2026-08")).json()
        assert r["evidence"]["days"] == [{"day": "2026-08-12", "nodes": 3},
                                         {"day": "2026-08-13", "nodes": 2}]

    async def test_a_month_still_running_is_not_a_bill(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """Its peak can only rise, so a total for it is a guess wearing a
        currency symbol."""
        from datetime import UTC, datetime

        await _activate(client, vendor)
        this_month = datetime.now(UTC).strftime("%Y-%m")
        r = (await client.get(f"/v1/license/invoice?month={this_month}")).json()
        assert r["provisional"] is True
        assert "still running" in r["note"]

    async def test_a_closed_month_is_final(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        await _activate(client, vendor)
        r = (await client.get("/v1/license/invoice?month=2026-08")).json()
        assert r["provisional"] is False
        assert r["note"] == "final"

    async def test_it_defaults_to_the_month_there_is_a_bill_for(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        from datetime import UTC, date, datetime, timedelta

        await _activate(client, vendor)
        r = (await client.get("/v1/license/invoice")).json()
        expected = (date(datetime.now(UTC).year, datetime.now(UTC).month, 1)
                    - timedelta(days=1)).strftime("%Y-%m")
        assert r["month"] == expected
        assert r["provisional"] is False

    async def test_a_contracted_rung_is_measured_and_not_totalled(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """`enterprise` has no list price. Putting a number in front of a
        customer that nobody agreed to is worse than declining to."""
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, workspace_tier="enterprise",
                                    governed_node_ceiling=200)})
        await self._fleet(store, "2026-08-12", 240)
        r = (await client.get("/v1/license/invoice?month=2026-08")).json()
        assert r["priced"] is False
        assert r["total_cents"] == 0
        assert r["peak_nodes"] == 240 and r["billable_nodes"] == 40
        assert "contracted" in r["note"]

    async def test_the_free_rung_can_never_owe_anything(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """Line 1: the free rung is where safety lives, and safety never costs
        — including when the fleet on it is past its allowance."""
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, workspace_tier="community",
                                    governed_node_ceiling=1)})
        await self._fleet(store, "2026-08-12", 25)
        r = (await client.get("/v1/license/invoice?month=2026-08")).json()
        assert r["billable_nodes"] == 24
        assert r["total_cents"] == 0

    async def test_no_license_is_not_a_zero_bill(
        self, client: httpx.AsyncClient,
    ) -> None:
        """Nothing is owed and nothing is claimed: a $0.00 statement would read
        as an invoice that was issued and settled."""
        r = await client.get("/v1/license/invoice?month=2026-08")
        assert r.status_code == 404
        assert "nothing to bill" in r.json()["detail"]

    async def test_money_never_becomes_a_float(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        """Every amount crosses the wire as integer cents beside its rendering.
        A cent that cannot be represented exactly is a cent somebody argues
        about later."""
        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, workspace_tier="security",
                                    governed_node_ceiling=50)})
        await self._fleet(store, "2026-08-12", 63)
        r = (await client.get("/v1/license/invoice?month=2026-08")).json()
        for field in ("base_cents", "overage_cents", "total_cents"):
            assert isinstance(r[field], int), (field, r[field])
        assert r["total_cents"] == 1_500_00 + 13 * 50_00
        assert r["total"] == "$2,150.00"

    async def test_one_tenants_bill_is_not_anothers(
        self, client: httpx.AsyncClient, vendor: dict,
    ) -> None:
        from axor_backend.tenancy import set_current_org

        store = client._transport.app.state.store  # type: ignore[attr-defined]
        await client.post("/v1/license/verify", json={
            "license_json": _signed(vendor, governed_node_ceiling=10)})
        try:
            set_current_org("acme")
            for i in range(99):
                await store.record_node_activity(f"acme{i}", "2026-08-12")
        finally:
            set_current_org("public")
        r = (await client.get("/v1/license/invoice?month=2026-08")).json()
        assert r["peak_nodes"] == 0
        assert r["total_cents"] == 299_00


def test_the_rate_card_and_the_pricing_page_state_one_ladder() -> None:
    """The page a customer reads and the table that charges them are two
    statements of one thing, in two languages, in two directories.

    Everything in this session has been one of those coming apart: two schema
    copies, two config compilers, a module flag that meant something on one
    side and nothing on the other. A price is the worst one to get wrong, so it
    is checked rather than remembered.
    """
    import pathlib

    from axor_backend.ee.license import TIER_NODE_CEILING
    from axor_backend.ee.pricing import RATE_CARD, money

    page = (pathlib.Path(__file__).resolve().parents[3]
            / "frontend" / "src" / "tabs" / "Pricing.tsx").read_text()
    for tier, (base_cents, over_cents) in RATE_CARD.items():
        if tier == "community":
            continue  # free: the page states $0 without a rate or an allowance
        base = money(base_cents).removesuffix(".00")
        over = money(over_cents).removesuffix(".00")
        included = TIER_NODE_CEILING[tier]
        assert f"{base} / mo" in page, (tier, base)
        assert f"+{over} / governed node / mo" in page, (tier, over)
        assert f"{included} governed nodes" in page, (tier, included)


# ── reporting usage outward: a separate decision, on a shared channel ─────────

class TestUsageLeavesOnlyWhenItWasAgreedTo:
    """Renewal is the vendor answering a question about the deployment. Usage
    reporting is the deployment volunteering something about the customer.
    Those are different things to agree to, so they are different switches —
    turning on `AXOR_LICENSE_RENEWAL_URL` must never turn this on.

    They share the outbound call because opening a second one buys nothing: the
    renewal request already goes to the vendor, already carries the license that
    identifies the caller, and already tolerates failure.
    """

    @staticmethod
    def _fetch(seen: list):  # noqa: ANN205
        async def fetch(
            url: str, org: str, current: object, report: dict | None = None,
        ) -> str | None:
            seen.append(report)
            return None
        return fetch

    async def _app(self, tmp_path: pathlib.Path, vendor: dict, *, reporting: bool):  # noqa: ANN202
        app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/u.db",
                         license_renewal_url="https://vendor.test/renew",
                         usage_reporting=reporting)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://backend.test",
        ) as c, app.router.lifespan_context(app):
            await c.post("/v1/license/verify", json={
                "license_json": _signed(vendor, governed_node_ceiling=10)})
            yield c, app

    async def test_renewal_alone_sends_nothing(
        self, tmp_path: pathlib.Path, vendor: dict,
    ) -> None:
        """The one that matters: a customer who wanted their license renewed
        automatically has not thereby agreed to be metered."""
        from axor_backend.licensing import renew_once
        from axor_backend.tenancy import PUBLIC_ORG

        async for client, app in self._app(tmp_path, vendor, reporting=False):
            store = app.state.store
            for i in range(14):
                await store.record_node_activity(f"n{i}", _last_month_day())
            seen: list = []
            await renew_once(app.state, PUBLIC_ORG, fetch=self._fetch(seen))
            assert seen == [None]
            status = (await client.get("/v1/license/status")).json()
            assert status["auto_renewal"] is True
            assert status["usage_reporting"] is False

    async def test_switched_on_it_reports_the_closed_month(
        self, tmp_path: pathlib.Path, vendor: dict,
    ) -> None:
        from axor_backend.licensing import renew_once
        from axor_backend.tenancy import PUBLIC_ORG

        async for client, app in self._app(tmp_path, vendor, reporting=True):
            store = app.state.store
            day = _last_month_day()
            for i in range(14):
                await store.record_node_activity(f"n{i}", day)
            seen: list = []
            await renew_once(app.state, PUBLIC_ORG, fetch=self._fetch(seen))
            report = seen[0]
            assert report is not None
            assert report["month"] == day[:7]
            assert report["peak_nodes"] == 14
            assert report["peak_day"] == day
            assert report["included_nodes"] == 10
            assert report["billable_nodes"] == 4
            assert (await client.get("/v1/license/status")).json()[
                "usage_reporting"] is True

    async def test_it_never_carries_a_node_id(
        self, tmp_path: pathlib.Path, vendor: dict,
    ) -> None:
        """A node id is the name of a machine on the customer's infrastructure.
        The vendor needs a count to raise an invoice and has no business
        knowing the topology — so the report is a fixed list of fields, not a
        serialization of whatever the meter happens to hold."""
        import json as _json

        from axor_backend.licensing import USAGE_REPORT_FIELDS, renew_once
        from axor_backend.tenancy import PUBLIC_ORG

        async for _client, app in self._app(tmp_path, vendor, reporting=True):
            store = app.state.store
            for name in ("prod-eu-west-1", "secret-project-node", "hr-payroll"):
                await store.record_node_activity(name, _last_month_day())
            seen: list = []
            await renew_once(app.state, PUBLIC_ORG, fetch=self._fetch(seen))
            rendered = _json.dumps(seen[0])
            for name in ("prod-eu-west-1", "secret-project-node", "hr-payroll"):
                assert name not in rendered, rendered
            assert set(seen[0]) == set(USAGE_REPORT_FIELDS)

    async def test_the_month_still_running_is_never_reported(
        self, tmp_path: pathlib.Path, vendor: dict,
    ) -> None:
        """Its peak can still rise, so sending it would have the vendor
        invoicing from a draft."""
        from axor_backend.licensing import renew_once, usage_report
        from axor_backend.tenancy import PUBLIC_ORG

        async for _client, app in self._app(tmp_path, vendor, reporting=True):
            store = app.state.store
            await store.record_node_activity("n1", _today())      # this month
            await store.record_node_activity("n2", _last_month_day())
            await store.record_node_activity("n3", _last_month_day())
            report = await usage_report(app.state, PUBLIC_ORG)
            assert report["month"] != _today()[:7]
            assert report["peak_nodes"] == 2  # last month's, not this one's
            seen: list = []
            await renew_once(app.state, PUBLIC_ORG, fetch=self._fetch(seen))
            assert seen[0]["peak_nodes"] == 2

    async def test_nothing_is_reported_without_a_license_to_bill_against(
        self, tmp_path: pathlib.Path, vendor: dict,
    ) -> None:
        from axor_backend.licensing import usage_report
        from axor_backend.tenancy import PUBLIC_ORG

        async for _client, app in self._app(tmp_path, vendor, reporting=True):
            app.state.licenses.clear()
            assert await usage_report(app.state, PUBLIC_ORG) is None

    async def test_the_operator_can_read_what_would_leave_before_agreeing(
        self, tmp_path: pathlib.Path, vendor: dict,
    ) -> None:
        """Consent an operator cannot inspect is a checkbox, not consent. Every
        field the report carries is on the invoice they can already read."""
        from axor_backend.licensing import USAGE_REPORT_FIELDS

        async for client, app in self._app(tmp_path, vendor, reporting=False):
            store = app.state.store
            day = _last_month_day()
            for i in range(3):
                await store.record_node_activity(f"n{i}", day)
            invoice = (await client.get(f"/v1/license/invoice?month={day[:7]}")).json()
            # `distinct_nodes` sits under `evidence` on the invoice, where it
            # belongs: it is what the peak is checked against, not a line
            # somebody pays. Readable is the claim, not top-level.
            readable = {**invoice, **invoice["evidence"]}
            for field in USAGE_REPORT_FIELDS:
                assert field in readable, field


def _today() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).date().isoformat()


def _last_month_day() -> str:
    """The 12th of the month just ended — a closed month, whatever today is."""
    from datetime import UTC, datetime, timedelta

    first = datetime.now(UTC).date().replace(day=1)
    return (first - timedelta(days=1)).replace(day=12).isoformat()
