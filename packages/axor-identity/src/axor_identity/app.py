"""The identity HTTP API.

Human login only: signup, login, refresh, logout, me, and org membership. It
issues EdDSA access tokens (verified downstream against the JWKS) and rotates
refresh tokens. Machine credentials (the control-plane's API keys, the Lab's
runtime ingest keys) are deliberately out of scope — those services keep them.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import secrets
from collections.abc import AsyncIterator
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, EmailStr, Field

from axor_identity import billing as bill
from axor_identity.keys import SigningKey, load_signing_key
from axor_identity.passwords import hash_password, needs_rehash, verify_password
from axor_identity.storage import Store, init_db, make_engine
from axor_identity.tokens import hash_refresh, issue_access, new_refresh
from axor_identity.verify import Claims, IdentityError, verify_access_token

# role rank (owner strongest); reused by the membership admin gate
ROLES = ("viewer", "member", "admin", "owner")
_ASSIGNABLE = frozenset({"admin", "member", "viewer"})  # owner is the org creator


def _rank(role: str) -> int:
    return ROLES.index(role) if role in ROLES else -1


def role_at_least(role: str, minimum: str) -> bool:
    return _rank(role) >= _rank(minimum)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _norm_email(email: str) -> str:
    return email.strip().lower()


# a real argon2 hash verified when the email is unknown, so a missing user and a
# wrong password cost the same wall-clock — no user-enumeration by timing.
_DUMMY_HASH = hash_password("timing-uniformity-placeholder")


# ── request bodies ────────────────────────────────────────────────────────────
class SignupBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    org_name: str = Field(min_length=1)


class LoginBody(BaseModel):
    email: EmailStr
    password: str
    org_id: str | None = None


class RefreshBody(BaseModel):
    refresh_token: str


class MemberBody(BaseModel):
    email: EmailStr
    role: str


class CheckoutBody(BaseModel):
    tier: str
    # where the payer lands afterwards: a page of the app that started the
    # checkout, on one of AXOR_IDENTITY_PUBLIC_ORIGINS
    return_url: str


class TierBody(BaseModel):
    tier: str


def create_app(database_url: str | None = None, *,
               signing_key: SigningKey | None = None,
               access_ttl: int | None = None,
               refresh_ttl: int | None = None,
               billing: bill.BillingConfig | None = None,
               paddle: bill.PaddleAPI | None = None,
               admin_token: str | None = None) -> FastAPI:
    url = (database_url or os.environ.get("AXOR_IDENTITY_DATABASE_URL")
           or "sqlite+aiosqlite:///./identity.db")
    signing_key = signing_key or load_signing_key()
    access_ttl = access_ttl if access_ttl is not None else int(
        os.environ.get("AXOR_IDENTITY_ACCESS_TTL", "900"))
    refresh_ttl = refresh_ttl if refresh_ttl is not None else int(
        os.environ.get("AXOR_IDENTITY_REFRESH_TTL", "1209600"))
    billing = billing if billing is not None else bill.load_config()
    if billing is not None and paddle is None:
        paddle = bill.PaddleClient(billing)
    # the path the apps' reverse proxies mount this service under
    public_path = os.environ.get("AXOR_IDENTITY_PUBLIC_PATH", "/identity").rstrip("/")
    admin_token = admin_token if admin_token is not None else os.environ.get(
        "AXOR_IDENTITY_ADMIN_TOKEN", "")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await init_db(app.state.store.engine)
        yield

    app = FastAPI(title="axor-identity", version="0.1.0", lifespan=lifespan)
    store = Store(make_engine(url))
    app.state.store = store
    app.state.signing_key = signing_key
    jwks = signing_key.jwks()

    async def _issue_session(user: dict, org_id: str, role: str, tier: str) -> dict:
        raw_refresh, token_hash = new_refresh()
        now = datetime.datetime.now(datetime.UTC)
        expires = (now + datetime.timedelta(seconds=refresh_ttl)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        await store.store_refresh(token_hash, user["user_id"], org_id, expires,
                                  _now_iso())
        access = issue_access(
            signing_key, user_id=user["user_id"], email=user["email"],
            org=org_id, role=role, tier=tier, ttl=access_ttl)
        return {
            "access_token": access,
            "refresh_token": raw_refresh,
            "token_type": "Bearer",
            "expires_in": access_ttl,
            "user": {"user_id": user["user_id"], "email": user["email"]},
            "org": {"org_id": org_id, "role": role, "tier": tier},
        }

    async def _caller(authorization: str | None = Header(default=None)) -> Claims:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "bearer access token required")
        token = authorization.split(" ", 1)[1].strip()
        try:
            return verify_access_token(token, jwks)
        except IdentityError as exc:
            raise HTTPException(401, str(exc)) from exc

    # ── auth ──────────────────────────────────────────────────────────────────
    @app.post("/v1/signup", status_code=201)
    async def signup(body: SignupBody) -> dict:
        email = _norm_email(body.email)
        if await store.get_user_by_email(email):
            raise HTTPException(409, "email already registered")
        user_id = "usr_" + secrets.token_hex(8)
        org_id = "org_" + secrets.token_hex(6)
        ts = _now_iso()
        await store.create_user(user_id, email, hash_password(body.password), ts)
        await store.create_org(org_id, body.org_name, "community", ts)
        await store.add_membership(user_id, org_id, "owner", ts)
        user = {"user_id": user_id, "email": email}
        return await _issue_session(user, org_id, "owner", "community")

    @app.post("/v1/login")
    async def login(body: LoginBody) -> dict:
        email = _norm_email(body.email)
        user = await store.get_user_by_email(email)
        ok = verify_password(user["password_hash"] if user else _DUMMY_HASH,
                             body.password)
        if user is None or not ok:
            raise HTTPException(401, "invalid email or password")
        # argon2 parameters may have hardened since this hash was written
        if needs_rehash(user["password_hash"]):
            await store.update_password(user["user_id"], hash_password(body.password))
        memberships = await store.memberships_for_user(user["user_id"])
        if not memberships:
            raise HTTPException(403, "user has no organization membership")
        chosen = _pick_org(memberships, body.org_id)
        return await _issue_session(user, chosen["org_id"], chosen["role"],
                                    chosen["tier"])

    @app.post("/v1/refresh")
    async def refresh(body: RefreshBody) -> dict:
        record = await store.get_refresh(hash_refresh(body.refresh_token))
        if record is None or record["revoked"]:
            raise HTTPException(401, "invalid refresh token")
        if record["expires_ts"] <= _now_iso():
            raise HTTPException(401, "refresh token expired")
        user = await store.get_user(record["user_id"])
        role = await store.role_in_org(record["user_id"], record["org_id"])
        org = await store.get_org(record["org_id"])
        if user is None or role is None or org is None:
            raise HTTPException(401, "refresh subject no longer valid")
        # rotate: the presented token is single-use
        await store.revoke_refresh(record["token_hash"])
        return await _issue_session(user, org["org_id"], role, org["tier"])

    @app.post("/v1/logout", status_code=204)
    async def logout(body: RefreshBody) -> None:
        await store.revoke_refresh(hash_refresh(body.refresh_token))

    @app.get("/v1/me")
    async def me(caller: Claims = Depends(_caller)) -> dict:
        user = await store.get_user(caller.user_id)
        if user is None:
            raise HTTPException(404, "user not found")
        memberships = await store.memberships_for_user(caller.user_id)
        return {
            "user": {"user_id": user["user_id"], "email": user["email"]},
            "active_org": caller.org,
            "role": caller.role,
            "memberships": memberships,
        }

    @app.post("/v1/orgs/{org_id}/members", status_code=201)
    async def add_member(org_id: str, body: MemberBody,
                         caller: Claims = Depends(_caller)) -> dict:
        if caller.org != org_id or not role_at_least(caller.role, "admin"):
            raise HTTPException(403, "admin of this organization required")
        if body.role not in _ASSIGNABLE:
            raise HTTPException(422, f"role must be one of {sorted(_ASSIGNABLE)}")
        target = await store.get_user_by_email(_norm_email(body.email))
        if target is None:
            raise HTTPException(404, "no user with that email")
        if await store.role_in_org(target["user_id"], org_id):
            raise HTTPException(409, "already a member")
        await store.add_membership(target["user_id"], org_id, body.role, _now_iso())
        return {"user_id": target["user_id"], "org_id": org_id, "role": body.role}

    # ── billing (see axor_identity.billing) ───────────────────────────────────
    def _billing() -> bill.BillingConfig:
        if billing is None:
            raise HTTPException(501, "billing is not configured")
        return billing

    def _billing_admin(caller: Claims) -> None:
        if not role_at_least(caller.role, "admin"):
            raise HTTPException(403, "owner or admin of this organization required")

    @app.get("/v1/billing/config")
    async def billing_config() -> dict:
        if billing is None:
            return {"enabled": False}
        return {"enabled": True, "environment": billing.environment,
                "client_token": billing.client_token,
                "tiers": [t for t in bill.TIERS if t in billing.prices]}

    @app.get("/v1/billing/pay", response_class=HTMLResponse)
    async def billing_pay() -> HTMLResponse:
        return HTMLResponse(bill.PAY_PAGE, headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})

    @app.get("/v1/billing/pay.js")
    async def billing_pay_js() -> Response:
        return Response(bill.PAY_SCRIPT, media_type="text/javascript",
                        headers={"Cache-Control": "no-store"})

    @app.get("/v1/billing/checkouts/{transaction_id}")
    async def billing_checkout_return(transaction_id: str) -> dict:
        record = await store.get_checkout(transaction_id)
        if record is None:
            raise HTTPException(404, "unknown checkout")
        return {"return_url": record["return_url"], "tier": record["tier"]}

    @app.post("/v1/billing/checkout", status_code=201)
    async def billing_checkout(body: CheckoutBody,
                               caller: Claims = Depends(_caller)) -> dict:
        config = _billing()
        _billing_admin(caller)
        price_id = config.prices.get(body.tier)
        if price_id is None:
            raise HTTPException(422, f"tier must be one of {sorted(config.prices)}")
        if not config.allowed_return(body.return_url):
            raise HTTPException(422, "return_url must be on one of this "
                                "deployment's app origins")
        current = await store.get_subscription(caller.org)
        if current and current["status"] in bill.ENTITLED_STATUSES:
            raise HTTPException(409, "this organization already has a "
                                "subscription — change plan in the billing portal")
        parts = urlsplit(body.return_url)
        pay_url = f"{parts.scheme}://{parts.netloc}{public_path}/v1/billing/pay"
        try:
            txn = await paddle.create_transaction(  # type: ignore[union-attr]
                price_id=price_id, org_id=caller.org,
                customer_id=current["customer_id"] if current else None,
                checkout_url=pay_url)
        except bill.BillingError as exc:
            bill.log.error("checkout failed for %s: %s", caller.org, exc)
            raise HTTPException(502, "the payment provider is unavailable") from exc
        await store.store_checkout(txn, caller.org, body.tier, body.return_url,
                                   _now_iso())
        return {"transaction_id": txn, "checkout_url": f"{pay_url}?_ptxn={txn}"}

    @app.get("/v1/billing/subscription")
    async def billing_subscription(caller: Claims = Depends(_caller)) -> dict:
        org = await store.get_org(caller.org)
        if org is None:
            raise HTTPException(404, "organization not found")
        sub = await store.get_subscription(caller.org)
        return {
            "enabled": billing is not None,
            "org_id": caller.org,
            "tier": org["tier"],
            # the tier in the caller's token: behind `tier` until its refresh
            "token_tier": caller.tier,
            "subscription": None if sub is None else {
                "status": sub["status"], "tier": sub["tier"],
                "current_period_end": sub["current_period_end"],
                "scheduled_change": sub["scheduled_change"]},
            # checkout and the portal are owner/admin actions
            "is_admin": role_at_least(caller.role, "admin"),
            "can_manage": bool(sub and sub["customer_id"])
                          and role_at_least(caller.role, "admin"),
        }

    @app.post("/v1/billing/portal")
    async def billing_portal(caller: Claims = Depends(_caller)) -> dict:
        _billing()
        _billing_admin(caller)
        sub = await store.get_subscription(caller.org)
        if sub is None or not sub["customer_id"]:
            raise HTTPException(404, "this organization has no subscription")
        try:
            url = await paddle.portal_url(  # type: ignore[union-attr]
                customer_id=sub["customer_id"],
                subscription_id=sub["subscription_id"])
        except bill.BillingError as exc:
            bill.log.error("portal failed for %s: %s", caller.org, exc)
            raise HTTPException(502, "the payment provider is unavailable") from exc
        return {"url": url}

    @app.post("/v1/billing/webhook")
    async def billing_webhook(request: Request) -> dict:
        config = _billing()
        raw = await request.body()
        if not bill.verify_signature(request.headers.get("Paddle-Signature"),
                                     raw, config.webhook_secret):
            raise HTTPException(401, "invalid signature")
        try:
            event = json.loads(raw)
            event_id = str(event["event_id"])
            event_type = str(event["event_type"])
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, "malformed event") from exc
        if not await store.record_event(event_id, event_type, _now_iso()):
            return {"ok": True, "duplicate": True}
        try:
            return await _apply_event(config, event, event_type)
        except Exception:
            # let the provider's retry run it again instead of being deduped
            await store.forget_event(event_id)
            raise

    async def _apply_event(config: bill.BillingConfig, event: dict,
                           event_type: str) -> dict:
        update = bill.parse_event(event)
        if update is None:
            return {"ok": True, "ignored": event_type}
        org_id = None
        if update.org_id and await store.get_org(update.org_id):
            org_id = update.org_id
        if org_id is None and update.subscription_id:
            org_id = await store.org_for_subscription(update.subscription_id)
        if org_id is None and update.customer_id:
            org_id = await store.org_for_customer(update.customer_id)
        if org_id is None:
            bill.log.warning("billing event %s names no known org", event_type)
            return {"ok": True, "ignored": "unknown org"}
        tier = bill.tier_for(update, config)
        if tier is None:
            bill.log.error("billing event %s for %s has price %r, which "
                           "AXOR_IDENTITY_PADDLE_PRICES does not sell",
                           event_type, org_id, update.price_id)
            return {"ok": True, "ignored": "unknown price"}
        values = {"customer_id": update.customer_id,
                  "subscription_id": update.subscription_id,
                  "price_id": update.price_id, "tier": config.tier_of_price[
                      update.price_id or ""], "status": update.status,
                  "current_period_end": update.current_period_end,
                  "scheduled_change": update.scheduled_change}
        if not event_type.startswith("subscription."):
            # a payment event knows nothing about the billing period or a
            # scheduled change: keep what the subscription events recorded
            values = {k: v for k, v in values.items() if v is not None}
        applied = await store.apply_subscription(
            org_id, tier=tier, values=values,
            event_at=bill.event_time(event.get("occurred_at")), ts=_now_iso())
        return {"ok": True, "org_id": org_id, "tier": tier, "applied": applied}

    @app.post("/v1/admin/orgs/{org_id}/tier")
    async def admin_set_tier(org_id: str, body: TierBody,
                             authorization: str | None = Header(default=None)) -> dict:
        """Operator grant — how a contracted (enterprise) org gets its tier."""
        presented = (authorization or "").removeprefix("Bearer ").strip()
        if not admin_token or not secrets.compare_digest(presented, admin_token):
            raise HTTPException(401, "operator token required")
        if body.tier not in bill.TIERS:
            raise HTTPException(422, f"tier must be one of {list(bill.TIERS)}")
        if not await store.set_org_tier(org_id, body.tier):
            raise HTTPException(404, "organization not found")
        return {"org_id": org_id, "tier": body.tier}

    # ── keys / health ─────────────────────────────────────────────────────────
    @app.get("/.well-known/jwks.json")
    async def jwks_doc() -> dict:
        return jwks

    @app.get("/v1/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    return app


def _pick_org(memberships: list[dict], org_id: str | None) -> dict:
    if org_id is not None:
        for m in memberships:
            if m["org_id"] == org_id:
                return m
        raise HTTPException(403, "not a member of that organization")
    if len(memberships) == 1:
        return memberships[0]
    raise HTTPException(
        400, "multiple organizations — specify org_id "
        f"(one of {[m['org_id'] for m in memberships]})")
