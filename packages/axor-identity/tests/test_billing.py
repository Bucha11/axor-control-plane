"""Billing: checkout opens a provider transaction for the caller's org, the
signed webhook moves the org's tier (and so the next token's), and the ordering,
dedup and misconfiguration cases fail safe.
"""
from __future__ import annotations

import json
import pathlib
import time

import httpx
import pytest
from axor_identity import billing as bill
from axor_identity.app import create_app
from axor_identity.keys import SigningKey, generate_pem, load_signing_key

PW = "correct horse battery"
SECRET = "pdl_ntfset_test_secret"
PRICES = {"team": "pri_team", "security": "pri_security"}
ORIGINS = ("https://plane.example", "https://lab.example")


class FakePaddle:
    def __init__(self) -> None:
        self.transactions: list[dict] = []
        self.portals: list[dict] = []
        self.fail = False

    async def create_transaction(self, **kwargs: object) -> str:
        if self.fail:
            raise bill.BillingError("down")
        self.transactions.append(kwargs)
        return f"txn_{len(self.transactions)}"

    async def portal_url(self, **kwargs: object) -> str:
        self.portals.append(kwargs)
        return "https://customer-portal.paddle.com/cpl_1"


def _config() -> bill.BillingConfig:
    return bill.BillingConfig(api_key="k", webhook_secret=SECRET, client_token="test_ct",
                              environment="sandbox", prices=PRICES,
                              public_origins=ORIGINS)


@pytest.fixture
def paddle() -> FakePaddle:
    return FakePaddle()


@pytest.fixture
async def client(tmp_path: pathlib.Path, paddle: FakePaddle):  # noqa: ANN201
    key: SigningKey = load_signing_key(generate_pem(), kid="k1")
    app = create_app(f"sqlite+aiosqlite:///{tmp_path}/id.db", signing_key=key,
                     billing=_config(), paddle=paddle, admin_token="op-token")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://identity") as c:
            yield c


async def _signup(client: httpx.AsyncClient, email: str = "ada@acme.io") -> dict:
    r = await client.post("/v1/signup",
                          json={"email": email, "password": PW, "org_name": "Acme"})
    assert r.status_code == 201, r.text
    return r.json()


def _auth(session: dict) -> dict:
    return {"Authorization": f"Bearer {session['access_token']}"}


_seq = iter(range(1, 10_000))


def _event(etype: str, data: dict, *, at: str = "2026-09-01T10:00:00Z") -> dict:
    return {"event_id": f"evt_{next(_seq)}", "event_type": etype,
            "occurred_at": at, "data": data}


def _sub(org_id: str | None, *, status: str = "active", price: str = "pri_team",
         sub_id: str = "sub_1", change: str | None = None) -> dict:
    return {"id": sub_id, "status": status, "customer_id": "ctm_1",
            "items": [{"price": {"id": price}, "quantity": 1}],
            "custom_data": {"org_id": org_id} if org_id else None,
            "current_billing_period": {"starts_at": "2026-09-01T10:00:00Z",
                                       "ends_at": "2026-10-01T10:00:00Z"},
            "scheduled_change": {"action": change} if change else None}


async def _deliver(client: httpx.AsyncClient, event: dict, *,
                   secret: str = SECRET, ts: int | None = None) -> httpx.Response:
    raw = json.dumps(event).encode()
    return await client.post("/v1/billing/webhook", content=raw, headers={
        "Paddle-Signature": bill.sign(raw, secret, ts),
        "Content-Type": "application/json"})


async def _tier_after_refresh(client: httpx.AsyncClient, session: dict) -> str:
    r = await client.post("/v1/refresh",
                          json={"refresh_token": session["refresh_token"]})
    assert r.status_code == 200, r.text
    session.update(r.json())
    return r.json()["org"]["tier"]


# ── signature ─────────────────────────────────────────────────────────────────
def test_signature_accepts_a_matching_h1_among_several() -> None:
    body = b'{"a":1}'
    ts = int(time.time())
    good = bill.sign(body, SECRET, ts).split("h1=")[1]
    assert bill.verify_signature(f"ts={ts};h1=deadbeef;h1={good}", body, SECRET)
    assert not bill.verify_signature(f"ts={ts};h1=deadbeef", body, SECRET)
    assert not bill.verify_signature(bill.sign(body, SECRET, ts), b'{"a":2}', SECRET)
    assert not bill.verify_signature(None, body, SECRET)


def test_signature_outside_tolerance_is_a_replay() -> None:
    body = b"{}"
    old = int(time.time()) - bill.SIGNATURE_TOLERANCE_S - 5
    assert not bill.verify_signature(bill.sign(body, SECRET, old), body, SECRET)


def test_event_times_compare_across_precisions() -> None:
    assert bill.event_time("2026-09-01T10:00:59Z") < bill.event_time(
        "2026-09-01T10:00:59.5Z")


def test_config_is_off_until_fully_configured_and_rejects_unknown_tiers() -> None:
    assert bill.load_config({}) is None
    env = {"AXOR_IDENTITY_PADDLE_API_KEY": "k",
           "AXOR_IDENTITY_PADDLE_WEBHOOK_SECRET": "s",
           "AXOR_IDENTITY_PADDLE_CLIENT_TOKEN": "c",
           "AXOR_IDENTITY_PADDLE_PRICES": '{"team": "pri_t"}',
           "AXOR_IDENTITY_PUBLIC_ORIGINS": "https://plane.example/"}
    config = bill.load_config(env)
    assert config and config.public_origins == ("https://plane.example",)
    with pytest.raises(ValueError, match="unknown tiers"):
        bill.load_config({**env, "AXOR_IDENTITY_PADDLE_PRICES": '{"community": "p"}'})
    with pytest.raises(ValueError, match="PUBLIC_ORIGINS"):
        bill.load_config({**env, "AXOR_IDENTITY_PUBLIC_ORIGINS": ""})


# ── checkout ──────────────────────────────────────────────────────────────────
async def test_config_route_is_public_and_names_the_buyable_tiers(
        client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/billing/config")
    assert r.json() == {"enabled": True, "environment": "sandbox",
                        "client_token": "test_ct", "tiers": ["team", "security"]}


async def test_checkout_opens_a_transaction_for_the_callers_org(
        client: httpx.AsyncClient, paddle: FakePaddle) -> None:
    session = await _signup(client)
    ret = "https://lab.example/#/workspace"
    r = await client.post("/v1/billing/checkout", headers=_auth(session),
                          json={"tier": "team", "return_url": ret})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["checkout_url"] == (
        "https://lab.example/identity/v1/billing/pay?_ptxn=txn_1")
    sent = paddle.transactions[0]
    assert sent["price_id"] == "pri_team"
    assert sent["org_id"] == session["org"]["org_id"]
    assert sent["checkout_url"] == "https://lab.example/identity/v1/billing/pay"
    # the pay page resolves the return URL by transaction id
    back = await client.get("/v1/billing/checkouts/txn_1")
    assert back.json() == {"return_url": ret, "tier": "team"}
    assert (await client.get("/v1/billing/checkouts/txn_nope")).status_code == 404


@pytest.mark.parametrize(("body", "status"), [
    ({"tier": "enterprise", "return_url": "https://plane.example/"}, 422),
    ({"tier": "community", "return_url": "https://plane.example/"}, 422),
    ({"tier": "team", "return_url": "https://evil.example/"}, 422),
    ({"tier": "team", "return_url": "javascript:alert(1)"}, 422),
])
async def test_checkout_refuses_unsellable_tiers_and_foreign_returns(
        client: httpx.AsyncClient, body: dict, status: int) -> None:
    session = await _signup(client)
    r = await client.post("/v1/billing/checkout", headers=_auth(session), json=body)
    assert r.status_code == status, r.text


async def test_checkout_needs_an_org_admin(client: httpx.AsyncClient) -> None:
    owner = await _signup(client)
    await _signup(client, "bob@else.io")
    r = await client.post(f"/v1/orgs/{owner['org']['org_id']}/members",
                          headers=_auth(owner),
                          json={"email": "bob@else.io", "role": "member"})
    assert r.status_code == 201
    login = await client.post("/v1/login", json={
        "email": "bob@else.io", "password": PW, "org_id": owner["org"]["org_id"]})
    r = await client.post("/v1/billing/checkout", headers=_auth(login.json()),
                          json={"tier": "team", "return_url": "https://plane.example/"})
    assert r.status_code == 403
    assert (await client.post("/v1/billing/checkout",
                              json={"tier": "team", "return_url": "x"})).status_code == 401


async def test_provider_outage_is_a_502_not_a_crash(
        client: httpx.AsyncClient, paddle: FakePaddle) -> None:
    session = await _signup(client)
    paddle.fail = True
    r = await client.post("/v1/billing/checkout", headers=_auth(session),
                          json={"tier": "team", "return_url": "https://plane.example/"})
    assert r.status_code == 502


# ── webhook → tier ────────────────────────────────────────────────────────────
async def test_active_subscription_upgrades_the_org_and_its_next_token(
        client: httpx.AsyncClient) -> None:
    session = await _signup(client)
    org = session["org"]["org_id"]
    r = await _deliver(client, _event("subscription.created", _sub(org)))
    assert r.json()["tier"] == "team", r.text
    assert await _tier_after_refresh(client, session) == "team"
    status = (await client.get("/v1/billing/subscription", headers=_auth(session))).json()
    assert status["tier"] == "team" and status["token_tier"] == "team"
    assert status["subscription"]["status"] == "active"
    assert status["subscription"]["current_period_end"] == "2026-10-01T10:00:00Z"
    assert status["can_manage"] is True and status["is_admin"] is True


async def test_first_payment_alone_grants_the_tier(client: httpx.AsyncClient) -> None:
    session = await _signup(client)
    org = session["org"]["org_id"]
    txn = {"id": "txn_1", "status": "completed", "subscription_id": "sub_9",
           "customer_id": "ctm_9", "custom_data": {"org_id": org},
           "items": [{"price": {"id": "pri_security"}}]}
    r = await _deliver(client, _event("transaction.completed", txn))
    assert r.json()["tier"] == "security"
    # a later subscription event without custom_data still finds the org
    r = await _deliver(client, _event("subscription.updated",
                                      _sub(None, sub_id="sub_9", price="pri_security"),
                                      at="2026-09-01T10:05:00Z"))
    assert r.json()["org_id"] == org


async def test_cancel_and_pause_fall_back_but_past_due_keeps_access(
        client: httpx.AsyncClient) -> None:
    session = await _signup(client)
    org = session["org"]["org_id"]
    await _deliver(client, _event("subscription.created", _sub(org)))
    r = await _deliver(client, _event("subscription.past_due",
                                      _sub(org, status="past_due"),
                                      at="2026-09-02T00:00:00Z"))
    assert r.json()["tier"] == "team"
    r = await _deliver(client, _event("subscription.canceled",
                                      _sub(org, status="canceled"),
                                      at="2026-09-03T00:00:00Z"))
    assert r.json()["tier"] == "community"
    assert await _tier_after_refresh(client, session) == "community"


async def test_scheduled_cancel_keeps_the_tier_until_it_happens(
        client: httpx.AsyncClient) -> None:
    session = await _signup(client)
    org = session["org"]["org_id"]
    await _deliver(client, _event("subscription.updated", _sub(org, change="cancel")))
    status = (await client.get("/v1/billing/subscription", headers=_auth(session))).json()
    assert status["tier"] == "team"
    assert status["subscription"]["scheduled_change"] == "cancel"


async def test_redelivery_is_acknowledged_once(client: httpx.AsyncClient) -> None:
    session = await _signup(client)
    event = _event("subscription.created", _sub(session["org"]["org_id"]))
    assert (await _deliver(client, event)).json()["applied"] is True
    assert (await _deliver(client, event)).json() == {"ok": True, "duplicate": True}


async def test_an_older_event_arriving_late_does_not_roll_back(
        client: httpx.AsyncClient) -> None:
    session = await _signup(client)
    org = session["org"]["org_id"]
    await _deliver(client, _event("subscription.canceled", _sub(org, status="canceled"),
                                  at="2026-09-05T00:00:00Z"))
    r = await _deliver(client, _event("subscription.created", _sub(org),
                                      at="2026-09-01T00:00:00Z"))
    assert r.json()["applied"] is False
    assert await _tier_after_refresh(client, session) == "community"


async def test_unsigned_stale_or_wrong_secret_deliveries_are_refused(
        client: httpx.AsyncClient) -> None:
    session = await _signup(client)
    event = _event("subscription.created", _sub(session["org"]["org_id"]))
    raw = json.dumps(event).encode()
    r = await client.post("/v1/billing/webhook", content=raw)
    assert r.status_code == 401
    assert (await _deliver(client, event, secret="other")).status_code == 401
    old = int(time.time()) - 3600
    assert (await _deliver(client, event, ts=old)).status_code == 401
    assert await _tier_after_refresh(client, session) == "community"


async def test_a_price_this_deployment_does_not_sell_changes_nothing(
        client: httpx.AsyncClient) -> None:
    session = await _signup(client)
    r = await _deliver(client, _event("subscription.created",
                                      _sub(session["org"]["org_id"], price="pri_other")))
    assert r.json()["ignored"] == "unknown price"
    assert await _tier_after_refresh(client, session) == "community"


async def test_events_that_move_no_entitlement_are_acknowledged(
        client: httpx.AsyncClient) -> None:
    r = await _deliver(client, _event("customer.updated", {"id": "ctm_1"}))
    assert r.json() == {"ok": True, "ignored": "customer.updated"}
    r = await _deliver(client, _event("subscription.created", _sub("org_missing")))
    assert r.json()["ignored"] == "unknown org"


async def test_a_subscribed_org_changes_plan_in_the_portal_not_a_second_checkout(
        client: httpx.AsyncClient, paddle: FakePaddle) -> None:
    session = await _signup(client)
    await _deliver(client, _event("subscription.created", _sub(session["org"]["org_id"])))
    r = await client.post("/v1/billing/checkout", headers=_auth(session),
                          json={"tier": "security", "return_url": "https://plane.example/"})
    assert r.status_code == 409
    r = await client.post("/v1/billing/portal", headers=_auth(session))
    assert r.json() == {"url": "https://customer-portal.paddle.com/cpl_1"}
    assert paddle.portals == [{"customer_id": "ctm_1", "subscription_id": "sub_1"}]


async def test_portal_without_a_subscription_is_404(client: httpx.AsyncClient) -> None:
    session = await _signup(client)
    assert (await client.post("/v1/billing/portal",
                              headers=_auth(session))).status_code == 404


# ── operator grant, pay page, disabled billing ────────────────────────────────
async def test_operator_grants_a_contracted_tier(client: httpx.AsyncClient) -> None:
    session = await _signup(client)
    org = session["org"]["org_id"]
    url = f"/v1/admin/orgs/{org}/tier"
    assert (await client.post(url, json={"tier": "enterprise"})).status_code == 401
    assert (await client.post(url, json={"tier": "enterprise"},
                              headers={"Authorization": "Bearer nope"})).status_code == 401
    ok = await client.post(url, json={"tier": "enterprise"},
                           headers={"Authorization": "Bearer op-token"})
    assert ok.status_code == 200
    assert await _tier_after_refresh(client, session) == "enterprise"
    bad = await client.post(url, json={"tier": "platinum"},
                            headers={"Authorization": "Bearer op-token"})
    assert bad.status_code == 422


async def test_pay_page_and_script_are_served(client: httpx.AsyncClient) -> None:
    page = await client.get("/v1/billing/pay?_ptxn=txn_1")
    assert page.status_code == 200 and "cdn.paddle.com/paddle/v2/paddle.js" in page.text
    script = await client.get("/v1/billing/pay.js")
    assert script.headers["content-type"].startswith("text/javascript")
    assert "Paddle.Initialize" in script.text


async def test_billing_off_answers_501_and_says_so(tmp_path: pathlib.Path) -> None:
    app = create_app(f"sqlite+aiosqlite:///{tmp_path}/off.db",
                     signing_key=load_signing_key(generate_pem(), kid="k"),
                     billing=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://identity") as c:
            assert (await c.get("/v1/billing/config")).json() == {"enabled": False}
            session = await _signup(c)
            r = await c.post("/v1/billing/checkout", headers=_auth(session),
                             json={"tier": "team", "return_url": "https://x/"})
            assert r.status_code == 501
            assert (await c.post("/v1/billing/webhook", content=b"{}")).status_code == 501
            status = (await c.get("/v1/billing/subscription",
                                  headers=_auth(session))).json()
            assert status["enabled"] is False and status["tier"] == "community"
