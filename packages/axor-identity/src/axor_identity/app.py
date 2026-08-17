"""The identity HTTP API.

Human login only: signup, login, refresh, logout, me, and org membership. It
issues EdDSA access tokens (verified downstream against the JWKS) and rotates
refresh tokens. Machine credentials (the control-plane's API keys, the Lab's
runtime ingest keys) are deliberately out of scope — those services keep them.
"""
from __future__ import annotations

import contextlib
import datetime
import os
import secrets
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field

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


def create_app(database_url: str | None = None, *,
               signing_key: SigningKey | None = None,
               access_ttl: int | None = None,
               refresh_ttl: int | None = None) -> FastAPI:
    url = (database_url or os.environ.get("AXOR_IDENTITY_DATABASE_URL")
           or "sqlite+aiosqlite:///./identity.db")
    signing_key = signing_key or load_signing_key()
    access_ttl = access_ttl if access_ttl is not None else int(
        os.environ.get("AXOR_IDENTITY_ACCESS_TTL", "900"))
    refresh_ttl = refresh_ttl if refresh_ttl is not None else int(
        os.environ.get("AXOR_IDENTITY_REFRESH_TTL", "1209600"))

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
