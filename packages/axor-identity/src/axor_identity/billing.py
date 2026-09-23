"""Billing for the hosted products, with Paddle as the payment provider.

One ladder, one bill: an org's `tier` (community | team | security |
enterprise) is the single entitlement. It rides in every access token, and the
Control Plane and the Lab both gate on it, so paying here upgrades both
products at once. This module is the only writer of that tier for paying orgs:

  checkout  POST /v1/billing/checkout  → a Paddle transaction for the org's
            chosen tier; the payer is sent to /v1/billing/pay on the app's own
            origin (both apps mount /identity), where Paddle.js runs the
            checkout and returns them to the app.
  webhook   POST /v1/billing/webhook   → Paddle's signed events. A subscription
            that is active, trialing or past_due (Paddle is still retrying the
            card) grants its price's tier; paused or canceled falls back to
            community. Redeliveries are acknowledged once by event id, and an
            event older than the last one applied is ignored.
  portal    POST /v1/billing/portal    → a Paddle customer-portal link, where
            the customer changes plan, updates the card, or cancels.

A tier change reaches a session at its next token refresh (the access TTL,
15 minutes by default); the apps refresh right after returning from checkout.

The server never sees a card. Paddle is the merchant of record.
"""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

log = logging.getLogger("axor_identity.billing")

# The ladder, lowest first. `community` is free and never bought; `enterprise`
# is contracted, so it is granted by an operator rather than checked out.
TIERS = ("community", "team", "security", "enterprise")
FREE_TIER = "community"
# Subscription statuses that keep the bought tier. past_due: Paddle is retrying
# the payment (dunning); cutting access on the first failed retry would punish
# an expired card harder than the provider does.
ENTITLED_STATUSES = frozenset({"active", "trialing", "past_due"})
SIGNATURE_TOLERANCE_S = 300

_API = {"sandbox": "https://sandbox-api.paddle.com",
        "production": "https://api.paddle.com"}


class BillingError(Exception):
    """A call to the payment provider failed."""


@dataclass(frozen=True)
class BillingConfig:
    api_key: str
    webhook_secret: str
    client_token: str
    environment: str
    prices: dict[str, str]  # tier -> price_id
    public_origins: tuple[str, ...]
    tier_of_price: dict[str, str] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tier_of_price",
                           {price: tier for tier, price in self.prices.items()})

    @property
    def api_base(self) -> str:
        return _API[self.environment]

    def allowed_return(self, url: str) -> bool:
        """A return URL must be on one of the apps' own origins — never an
        arbitrary site the checkout would forward a paying user to."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        return parts.scheme in ("https", "http") and origin in self.public_origins


def load_config(env: dict[str, str] | None = None) -> BillingConfig | None:
    """Billing is on when the API key, webhook secret, client token and at least
    one price are configured; otherwise None and the routes answer 501."""
    env = dict(os.environ if env is None else env)
    api_key = env.get("AXOR_IDENTITY_PADDLE_API_KEY", "")
    secret = env.get("AXOR_IDENTITY_PADDLE_WEBHOOK_SECRET", "")
    client_token = env.get("AXOR_IDENTITY_PADDLE_CLIENT_TOKEN", "")
    raw_prices = env.get("AXOR_IDENTITY_PADDLE_PRICES", "")
    if not (api_key and secret and client_token and raw_prices):
        return None
    environment = env.get("AXOR_IDENTITY_PADDLE_ENVIRONMENT", "sandbox")
    if environment not in _API:
        raise ValueError(
            f"AXOR_IDENTITY_PADDLE_ENVIRONMENT must be one of {sorted(_API)}")
    prices = json.loads(raw_prices)
    if not isinstance(prices, dict) or not prices:
        raise ValueError('AXOR_IDENTITY_PADDLE_PRICES must be {"team": "pri_…", …}')
    bad = sorted(set(prices) - (set(TIERS) - {FREE_TIER}))
    if bad:
        raise ValueError(f"AXOR_IDENTITY_PADDLE_PRICES names unknown tiers: {bad}")
    origins = tuple(o.strip().rstrip("/") for o in
                    env.get("AXOR_IDENTITY_PUBLIC_ORIGINS", "").split(",") if o.strip())
    if not origins:
        raise ValueError("AXOR_IDENTITY_PUBLIC_ORIGINS is required with billing on "
                         "(e.g. https://plane.useaxor.net,https://lab.useaxor.net)")
    return BillingConfig(api_key=api_key, webhook_secret=secret,
                         client_token=client_token, environment=environment,
                         prices={str(k): str(v) for k, v in prices.items()},
                         public_origins=origins)


# ── webhook signature ─────────────────────────────────────────────────────────
def verify_signature(header: str | None, body: bytes, secret: str, *,
                     now: float | None = None) -> bool:
    """Paddle-Signature: `ts=<unix>;h1=<hex hmac-sha256 of "<ts>:<body>">`.
    Several h1 values may be present while a secret is being rotated; any one
    matching is enough. A timestamp outside the tolerance is a replay."""
    if not header:
        return False
    ts, candidates = None, []
    for part in header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == "ts":
            ts = value
        elif key == "h1":
            candidates.append(value)
    if ts is None or not ts.isdigit() or not candidates:
        return False
    if abs((now if now is not None else time.time()) - int(ts)) > SIGNATURE_TOLERANCE_S:
        return False
    expected = hmac.new(secret.encode(), ts.encode() + b":" + body,
                        hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, c) for c in candidates)


def sign(body: bytes, secret: str, ts: int | None = None) -> str:
    """The header Paddle would send for `body` — for tests and local replays."""
    ts = int(time.time()) if ts is None else ts
    digest = hmac.new(secret.encode(), f"{ts}:".encode() + body,
                      hashlib.sha256).hexdigest()
    return f"ts={ts};h1={digest}"


# ── provider API ──────────────────────────────────────────────────────────────
class PaddleAPI(Protocol):
    async def create_transaction(self, *, price_id: str, org_id: str,
                                 customer_id: str | None,
                                 checkout_url: str) -> str: ...

    async def portal_url(self, *, customer_id: str,
                         subscription_id: str | None) -> str: ...


class PaddleClient:
    """The two Paddle Billing calls this service makes, over stdlib HTTP."""

    def __init__(self, config: BillingConfig) -> None:
        self._config = config

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            self._config.api_base + path, data=json.dumps(payload).encode(),
            method="POST", headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:500].decode(errors="replace")
            raise BillingError(f"paddle {path}: HTTP {exc.code} {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise BillingError(f"paddle {path}: {exc}") from exc

    async def create_transaction(self, *, price_id: str, org_id: str,
                                 customer_id: str | None,
                                 checkout_url: str) -> str:
        payload: dict[str, Any] = {
            "items": [{"price_id": price_id, "quantity": 1}],
            # copied onto the subscription Paddle creates, so every later
            # subscription event names the org it belongs to
            "custom_data": {"org_id": org_id},
            "checkout": {"url": checkout_url},
        }
        if customer_id:
            payload["customer_id"] = customer_id
        data = (await asyncio.to_thread(self._post, "/transactions", payload))["data"]
        return str(data["id"])

    async def portal_url(self, *, customer_id: str,
                         subscription_id: str | None) -> str:
        payload = {"subscription_ids": [subscription_id] if subscription_id else []}
        data = (await asyncio.to_thread(
            self._post, f"/customers/{customer_id}/portal-sessions", payload))["data"]
        return str(data["urls"]["general"]["overview"])


# ── event → subscription state ────────────────────────────────────────────────
@dataclass(frozen=True)
class SubscriptionUpdate:
    org_id: str | None
    subscription_id: str | None
    customer_id: str | None
    price_id: str | None
    status: str
    current_period_end: str | None
    scheduled_change: str | None


def parse_event(event: dict[str, Any]) -> SubscriptionUpdate | None:
    """The subscription state an event describes, or None for an event that
    moves no entitlement."""
    etype = str(event.get("event_type", ""))
    data = event.get("data") or {}
    custom = data.get("custom_data") or {}
    items = data.get("items") or []
    price_id = None
    if items:
        price = items[0].get("price") or {}
        price_id = price.get("id") or items[0].get("price_id")
    if etype.startswith("subscription."):
        period = data.get("current_billing_period") or {}
        change = data.get("scheduled_change") or {}
        return SubscriptionUpdate(
            org_id=custom.get("org_id"), subscription_id=data.get("id"),
            customer_id=data.get("customer_id"), price_id=price_id,
            status=str(data.get("status", "")),
            current_period_end=period.get("ends_at"),
            scheduled_change=change.get("action"))
    if etype == "transaction.completed" and data.get("subscription_id"):
        # the first payment: the subscription is live even if its own
        # subscription.created/activated delivery is still in flight
        return SubscriptionUpdate(
            org_id=custom.get("org_id"), subscription_id=data.get("subscription_id"),
            customer_id=data.get("customer_id"), price_id=price_id,
            status="active", current_period_end=None, scheduled_change=None)
    return None


def tier_for(update: SubscriptionUpdate, config: BillingConfig) -> str | None:
    """The tier a subscription state grants; None when its price is not one
    this deployment sells (a misconfiguration — never guessed at)."""
    tier = config.tier_of_price.get(update.price_id or "")
    if tier is None:
        return None
    return tier if update.status in ENTITLED_STATUSES else FREE_TIER


def event_time(value: object) -> str:
    """An event's occurred_at in one fixed-width form, so two of them compare
    correctly as strings ("…59Z" would otherwise sort after "…59.5Z")."""
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.datetime.now(datetime.UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.UTC)
    return parsed.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


PAY_PAGE = (Path(__file__).parent / "static" / "pay.html").read_text()
PAY_SCRIPT = (Path(__file__).parent / "static" / "pay.js").read_text()
