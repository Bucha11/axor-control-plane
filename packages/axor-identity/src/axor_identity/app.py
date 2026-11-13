"""The identity HTTP API.

Human login only: signup, login (password, or GitHub / Google — see
``oauth.py``), refresh, logout, me, and org membership. It
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
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from axor_identity.keys import SigningKey, load_signing_key
from axor_identity.oauth import OAuthError, Provider, digest, pkce_pair, providers_from_env
from axor_identity.passwords import hash_password, needs_rehash, verify_password
from axor_identity.storage import Store, init_db, make_engine
from axor_identity.tokens import hash_refresh, issue_access, new_refresh
from axor_identity.verify import Claims, IdentityError, verify_access_token

# role rank (owner strongest); reused by the membership admin gate
ROLES = ("viewer", "member", "admin", "owner")
_ASSIGNABLE = frozenset({"admin", "member", "viewer"})  # owner is the org creator

# Who may create an account. A self-hosted install is single-tenant: one company
# runs it for itself, and an open /v1/signup on a public hostname lets anyone
# passing by create an org on their server. Hosted deployments want the opposite.
#
# - open        anyone may sign up. The hosted default, and what every
#               deployment did before this setting existed.
# - first-only  the first account on an empty install succeeds; every later one
#               is refused. The operator claims the install, then it is shut.
# - closed      no signup at all. Accounts are added by an org admin through
#               POST /v1/orgs/{org_id}/members, which needs an existing one.
#
# Closing signup is NOT closing the install: login, refresh and membership are
# untouched, so the admin keeps adding colleagues by e-mail.
SIGNUP_MODES = ("open", "first-only", "closed")


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


class ExchangeBody(BaseModel):
    code: str = Field(min_length=16, max_length=128)
    org_id: str | None = None


# an in-flight provider sign-in, and the code the browser trades for a session
_STATE_TTL = 600
_CODE_TTL = 120


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


def _with_query(url: str, **params: str) -> str:
    """Add query parameters ahead of any #fragment — the apps route by hash,
    so the fragment is theirs and the code rides in the query."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k not in params]
    query += list(params.items())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query),
                       parts.fragment))


def create_app(database_url: str | None = None, *,
               signing_key: SigningKey | None = None,
               access_ttl: int | None = None,
               refresh_ttl: int | None = None,
               signup: str | None = None,
               providers: dict[str, Provider] | None = None,
               public_url: str | None = None,
               return_origins: list[str] | None = None,
               oauth_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    url = (database_url or os.environ.get("AXOR_IDENTITY_DATABASE_URL")
           or "sqlite+aiosqlite:///./identity.db")
    signing_key = signing_key or load_signing_key()
    access_ttl = access_ttl if access_ttl is not None else int(
        os.environ.get("AXOR_IDENTITY_ACCESS_TTL", "900"))
    refresh_ttl = refresh_ttl if refresh_ttl is not None else int(
        os.environ.get("AXOR_IDENTITY_REFRESH_TTL", "1209600"))
    signup_mode = (signup or os.environ.get("AXOR_IDENTITY_SIGNUP") or "open").strip()
    if signup_mode not in SIGNUP_MODES:
        # Fail loudly at construction. A typo here is a security setting that
        # silently did nothing, and the operator would only learn of it from a
        # stranger's account appearing on their install.
        raise ValueError(
            f"AXOR_IDENTITY_SIGNUP must be one of {list(SIGNUP_MODES)}, "
            f"got {signup_mode!r}")

    # Sign in with GitHub / Google. The callback URL the provider is registered
    # with is <public_url>/v1/oauth/<provider>/callback, so without a public URL
    # no provider is offered. Where the browser may be sent back to: the public
    # URL's own origin, plus AXOR_IDENTITY_RETURN_ORIGINS (the Lab's, say, when
    # one identity serves both). Anything else is refused — an open redirect on
    # a login service hands its codes to whoever wrote the link.
    public_url = (public_url if public_url is not None
                  else os.environ.get("AXOR_IDENTITY_PUBLIC_URL", "")).strip().rstrip("/")
    providers = (providers if providers is not None else providers_from_env()) \
        if public_url else {}
    if return_origins is None:
        return_origins = [o for o in (os.environ.get("AXOR_IDENTITY_RETURN_ORIGINS") or "")
                          .replace(" ", ",").split(",") if o]
    allowed_origins = {_origin(public_url)} | {_origin(o) for o in return_origins}
    allowed_origins.discard("")

    def _safe_return(return_to: str) -> str | None:
        if return_to.startswith("/") and not return_to.startswith("//") \
                and "\\" not in return_to:
            return _origin(public_url) + return_to
        return return_to if _origin(return_to) in allowed_origins else None

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
        # The gate runs before the e-mail is even looked at, so a refused signup
        # cannot be used to probe which addresses are registered.
        if signup_mode == "closed":
            raise HTTPException(403, "signup is closed on this installation")
        if signup_mode == "first-only" and await store.count_users():
            raise HTTPException(
                403, "signup is closed: this installation already has an account")
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
        # an account made through GitHub / Google has no password: it costs the
        # same as a wrong one and answers the same
        stored = user["password_hash"] if user and user["password_hash"] else None
        ok = verify_password(stored or _DUMMY_HASH, body.password)
        if stored is None or not ok:
            raise HTTPException(401, "invalid email or password")
        # argon2 parameters may have hardened since this hash was written
        if needs_rehash(stored):
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
            "user": {"user_id": user["user_id"], "email": user["email"],
                     "email_verified": user["email_verified"],
                     "has_password": bool(user["password_hash"]),
                     "providers": await store.identities_for_user(caller.user_id)},
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

    # ── sign in with GitHub / Google ──────────────────────────────────────────
    def _provider(name: str) -> Provider:
        provider = providers.get(name)
        if provider is None:
            raise HTTPException(404, f"sign-in with {name!r} is not set up here")
        return provider

    def _callback(provider: Provider) -> str:
        return f"{public_url}/v1/oauth/{provider.name}/callback"

    @app.get("/v1/oauth/providers")
    async def oauth_providers() -> dict:
        """The buttons a login form should show. Empty unless the operator set
        a provider's client id and secret and AXOR_IDENTITY_PUBLIC_URL."""
        return {"providers": [{"name": p.name, "label": p.label}
                              for p in providers.values()]}

    @app.get("/v1/oauth/{name}/start")
    async def oauth_start(name: str, return_to: str = "/") -> RedirectResponse:
        provider = _provider(name)
        target = _safe_return(return_to)
        if target is None:
            raise HTTPException(400, "return_to is not an address this service sends logins to")
        await store.sweep_expired(_now_iso())
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        expires = (datetime.datetime.now(datetime.UTC)
                   + datetime.timedelta(seconds=_STATE_TTL)).strftime("%Y-%m-%dT%H:%M:%SZ")
        await store.put_state(digest(state), provider.name, verifier, target, expires)
        return RedirectResponse(provider.authorize(
            redirect_uri=_callback(provider), state=state, challenge=challenge), 302)

    @app.get("/v1/oauth/{name}/callback")
    async def oauth_callback(name: str, state: str = "", code: str = "",
                             error: str = "") -> RedirectResponse:
        provider = _provider(name)
        flight = await store.take_state(digest(state)) if state else None
        if flight is None or flight["provider"] != provider.name \
                or flight["expires_ts"] <= _now_iso():
            # no return address we trust: nowhere to send the browser but here
            raise HTTPException(400, "this sign-in link has expired — start again")
        back = flight["return_to"]
        if error or not code:
            return RedirectResponse(_with_query(
                back, axor_login_error=f"{provider.label} sign-in was cancelled"), 302)
        try:
            async with httpx.AsyncClient(transport=oauth_transport, timeout=15) as http:
                token = await provider.exchange(
                    http, code=code, redirect_uri=_callback(provider),
                    verifier=flight["verifier"])
                profile = await provider.profile(http, token)
            user_id, created = await _account_for(provider, profile)
        except (OAuthError, httpx.HTTPError) as exc:
            message = str(exc) if isinstance(exc, OAuthError) \
                else f"{provider.label} could not be reached"
            return RedirectResponse(_with_query(back, axor_login_error=message), 302)
        except HTTPException as exc:
            return RedirectResponse(_with_query(back, axor_login_error=str(exc.detail)), 302)
        raw = secrets.token_urlsafe(32)
        expires = (datetime.datetime.now(datetime.UTC)
                   + datetime.timedelta(seconds=_CODE_TTL)).strftime("%Y-%m-%dT%H:%M:%SZ")
        await store.put_login_code(digest(raw), user_id, created, expires)
        return RedirectResponse(_with_query(back, axor_login=raw), 302)

    async def _account_for(provider: Provider, profile) -> tuple[str, bool]:  # noqa: ANN001
        """The account this provider identity signs into, made if need be.

        1. An identity linked before → its account, whatever the address is now.
        2. An account with the same address → linked to it. If that account's
           address was never verified, whoever set its password never proved
           they own the address — and the provider just did. The password is
           dropped and its sessions revoked, so an account pre-registered under
           someone else's address cannot be kept by the person who did that.
        3. Neither → a new account and workspace, if this install takes signups.
        """
        ts = _now_iso()
        linked = await store.user_for_identity(provider.name, profile.subject)
        if linked is not None:
            return linked, False
        existing = await store.get_user_by_email(profile.email)
        if existing is not None:
            if not existing["email_verified"]:
                await store.mark_verified(existing["user_id"],
                                          drop_password=bool(existing["password_hash"]))
                await store.revoke_all_refresh(existing["user_id"])
            await store.link_identity(provider.name, profile.subject,
                                      existing["user_id"], profile.email, ts)
            return existing["user_id"], False
        if signup_mode == "closed" or (
                signup_mode == "first-only" and await store.count_users()):
            raise HTTPException(403, "no account with this e-mail, and this "
                                     "installation does not take new ones")
        user_id = "usr_" + secrets.token_hex(8)
        org_id = "org_" + secrets.token_hex(6)
        name = (profile.display or profile.email.split("@")[0])[:150]
        await store.create_user(user_id, profile.email, None, ts, email_verified=True)
        await store.create_org(org_id, f"{name}'s workspace", "community", ts)
        await store.add_membership(user_id, org_id, "owner", ts)
        await store.link_identity(provider.name, profile.subject, user_id,
                                  profile.email, ts)
        return user_id, True

    @app.post("/v1/oauth/exchange")
    async def oauth_exchange(body: ExchangeBody) -> dict:
        """The code from the redirect, traded once for a session. ``created``
        says whether the sign-in made a new account (the app counts sign-ups
        by it)."""
        record = await store.take_login_code(digest(body.code))
        if record is None or record["expires_ts"] <= _now_iso():
            raise HTTPException(401, "this sign-in has expired — start again")
        user = await store.get_user(record["user_id"])
        memberships = await store.memberships_for_user(record["user_id"]) if user else []
        if user is None or not memberships:
            raise HTTPException(403, "user has no organization membership")
        # no form to name an org on the way back from a provider: the one asked
        # for if the user belongs to it, else the first they joined
        chosen = next((m for m in memberships if m["org_id"] == body.org_id), memberships[0])
        session = await _issue_session(user, chosen["org_id"], chosen["role"],
                                       chosen["tier"])
        return {**session, "created": record["created"]}

    # ── keys / health ─────────────────────────────────────────────────────────
    @app.get("/.well-known/jwks.json")
    async def jwks_doc() -> dict:
        return jwks

    @app.get("/v1/signup-mode")
    async def signup_mode_doc() -> dict:
        """Whether this installation accepts new accounts, so a client can hide
        the form instead of offering one that answers 403. `first-only` reports
        whether the one account has already been taken."""
        available = signup_mode == "open" or (
            signup_mode == "first-only" and not await store.count_users())
        return {"mode": signup_mode, "available": available}

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
