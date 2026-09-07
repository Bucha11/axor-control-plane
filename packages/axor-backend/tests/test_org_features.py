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
    def _serving(license_json: str | None):  # noqa: ANN205
        async def fetch(url: str, org: str, current) -> str | None:  # noqa: ANN001
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

        async def broken(url: str, org: str, current) -> str:  # noqa: ANN001
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
    def test_a_paid_license_needs_a_real_ceiling(self) -> None:
        """The ceiling used to be decorative, so a zero passed unnoticed. It is
        compared to the live fleet on every sweep now, and 0 warns forever about
        a customer who has paid."""
        from axor_backend.ee.cli import main

        assert main(["issue", "--key", _vendor_priv(), "--org", "T",
                     "--expires-at", "2099-01-01"]) == 2
        assert main(["issue", "--key", _vendor_priv(), "--org", "T",
                     "--governed-nodes", "25",
                     "--expires-at", "2099-01-01"]) == 0

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
