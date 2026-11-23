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
import logging
import os
import secrets
from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from axor_identity import sso
from axor_identity.keys import SigningKey, load_signing_key
from axor_identity.mail import Mailer, mailer_from_env
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
    # a new workspace's name — or, with ``invite``, none: the account joins the
    # inviting organization instead of starting one
    org_name: str = ""
    invite: str | None = None
    # where the verification link should open (an app page); see _safe_return
    return_to: str | None = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str
    org_id: str | None = None


class RefreshBody(BaseModel):
    refresh_token: str


class MemberBody(BaseModel):
    email: EmailStr
    role: str


class RoleBody(BaseModel):
    role: str


class InviteBody(BaseModel):
    role: str = "member"
    email: EmailStr | None = None       # for one address; None = whoever has the link
    return_to: str | None = None        # the app page the link opens (see _safe_return)


class LinkBody(BaseModel):
    return_to: str | None = None


class TokenBody(BaseModel):
    token: str = Field(min_length=16, max_length=128)


class ForgotBody(BaseModel):
    email: EmailStr
    return_to: str | None = None


class ResetBody(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    password: str = Field(min_length=8)


# how long an e-mailed link stays good, and how often one may be sent
_VERIFY_TTL = 24 * 3600
_RESET_TTL = 3600
_MAIL_GAP = 60
_INVITE_TTL = 7 * 24 * 3600


class SSOBody(BaseModel):
    issuer: str = Field(min_length=8, max_length=500)
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str | None = Field(default=None, max_length=500)  # kept if left out
    domains: list[str] = Field(min_length=1, max_length=20)
    enforce: bool = False
    default_role: str = "member"


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
               oauth_transport: httpx.AsyncBaseTransport | None = None,
               mailer: Mailer | None | bool = True) -> FastAPI:
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

    # True = from the environment (AXOR_IDENTITY_SMTP_URL); None/False = no mail
    mail: Mailer | None = mailer_from_env() if mailer is True else (mailer or None)
    last_mail: dict[str, float] = {}  # (purpose:user) → when, for _MAIL_GAP

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
            org=org_id, role=role, tier=tier, ttl=access_ttl,
            email_verified=bool(user.get("email_verified")))
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
        if body.invite:
            return await _signup_invited(body)
        if not body.org_name.strip():
            raise HTTPException(422, "a new workspace needs a name")
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
        await _send_link(user, "verify", body.return_to, quiet=True)
        return await _issue_session(user, org_id, "owner", "community")

    @app.post("/v1/login")
    async def login(body: LoginBody) -> dict:
        email = _norm_email(body.email)
        user = await store.get_user_by_email(email)
        await _sso_enforced(email, user)
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

    @app.get("/v1/orgs/{org_id}/members")
    async def list_members(org_id: str, caller: Claims = Depends(_caller)) -> dict:
        if caller.org != org_id:
            raise HTTPException(403, "a member of this organization required")
        return {"members": await store.org_members(org_id)}

    # ── a member's role, changed or taken away ────────────────────────────────
    # An admin (or the owner) sets anyone's role among admin / member / viewer,
    # or removes them. Never their own — a slip would lock them out — and never
    # the owner's: the owner created the workspace and is its one way back in.
    # The new role reaches the member at their next token refresh (minutes).

    async def _changeable(org_id: str, user_id: str, caller: Claims) -> None:
        if caller.org != org_id or not role_at_least(caller.role, "admin"):
            raise HTTPException(403, "admin of this organization required")
        if user_id == caller.user_id:
            raise HTTPException(409, "you cannot change your own role")
        current = await store.role_in_org(user_id, org_id)
        if current is None:
            raise HTTPException(404, "not a member of this organization")
        if current == "owner":
            raise HTTPException(409, "the owner's role cannot be changed")

    @app.put("/v1/orgs/{org_id}/members/{user_id}")
    async def set_member_role(org_id: str, user_id: str, body: RoleBody,
                              caller: Claims = Depends(_caller)) -> dict:
        if body.role not in _ASSIGNABLE:
            raise HTTPException(422, f"role must be one of {sorted(_ASSIGNABLE)}")
        await _changeable(org_id, user_id, caller)
        await store.set_member_role(user_id, org_id, body.role)
        return {"user_id": user_id, "org_id": org_id, "role": body.role}

    @app.delete("/v1/orgs/{org_id}/members/{user_id}")
    async def remove_member(org_id: str, user_id: str,
                            caller: Claims = Depends(_caller)) -> dict:
        await _changeable(org_id, user_id, caller)
        await store.remove_member(user_id, org_id)
        return {"removed": user_id}

    # ── invites ───────────────────────────────────────────────────────────────
    # A link an admin sends; whoever opens it signs up (or in) and joins the
    # organization with the role it names. Single use, seven days, revocable;
    # bound to one address when the admin names one. It is how a team joins a
    # workspace: adding a member used to need them to have signed up first.

    async def _open_invite(token: str) -> dict:
        record = await store.invite_by_hash(digest(token))
        if record is None or record["revoked"] or record["used_by"] \
                or record["expires_ts"] <= _now_iso():
            raise HTTPException(404, "this invitation has expired, was used, or was withdrawn")
        return record

    @app.post("/v1/orgs/{org_id}/invites", status_code=201)
    async def create_invite(org_id: str, body: InviteBody,
                            caller: Claims = Depends(_caller)) -> dict:
        if caller.org != org_id or not role_at_least(caller.role, "admin"):
            raise HTTPException(403, "admin of this organization required")
        if body.role not in _ASSIGNABLE:
            raise HTTPException(422, f"role must be one of {sorted(_ASSIGNABLE)}")
        email = _norm_email(body.email) if body.email else None
        if email:
            existing = await store.get_user_by_email(email)
            if existing and await store.role_in_org(existing["user_id"], org_id):
                raise HTTPException(409, "already a member")
        raw = secrets.token_urlsafe(24)
        invite_id = "inv_" + secrets.token_hex(6)
        expires = _expiry(_INVITE_TTL)
        await store.create_invite(invite_id, digest(raw), org_id, body.role, email,
                                  caller.user_id, _now_iso(), expires)
        target = _safe_return(body.return_to or "/") if (public_url and body.return_to) else None
        link = _with_query(target, axor_invite=raw) if target else None
        mailed = False
        if email and link and mail is not None:
            org = await store.get_org(org_id)
            inviter = await store.get_user(caller.user_id)
            try:
                await mail.send(email, f"Join {org['name'] if org else 'a workspace'} on Axor",
                                f"{inviter['email'] if inviter else 'Someone'} invited you to "
                                f"{org['name'] if org else 'their workspace'} on Axor, as "
                                f"{body.role}:\n\n{link}\n\nThe link works once, for seven "
                                "days.")
                mailed = True
            except Exception as exc:  # noqa: BLE001 - the link still works
                logging.getLogger("axor_identity.mail").warning("invite mail failed: %s", exc)
        return {"invite_id": invite_id, "token": raw, "link": link, "role": body.role,
                "email": email, "expires_ts": expires, "mailed": mailed}

    @app.get("/v1/orgs/{org_id}/invites")
    async def list_invites(org_id: str, caller: Claims = Depends(_caller)) -> dict:
        if caller.org != org_id or not role_at_least(caller.role, "admin"):
            raise HTTPException(403, "admin of this organization required")
        return {"invites": await store.open_invites(org_id, _now_iso())}

    @app.delete("/v1/orgs/{org_id}/invites/{invite_id}", status_code=204)
    async def revoke_invite(org_id: str, invite_id: str,
                            caller: Claims = Depends(_caller)) -> None:
        if caller.org != org_id or not role_at_least(caller.role, "admin"):
            raise HTTPException(403, "admin of this organization required")
        if not await store.revoke_invite(org_id, invite_id):
            raise HTTPException(404, "no open invitation by that id")

    @app.get("/v1/invites/{token}")
    async def invite_info(token: str) -> dict:
        """What the link is for — shown before anyone signs up. No address of
        the inviter: the org's name and the role are enough to decide."""
        record = await _open_invite(token)
        org = await store.get_org(record["org_id"])
        return {"org_name": org["name"] if org else "", "role": record["role"],
                "for_email": record["email"], "expires_ts": record["expires_ts"]}

    async def _join(record: dict, token: str, user: dict) -> dict:
        if record["email"] and record["email"] != user["email"]:
            raise HTTPException(403, "this invitation is for another address")
        if not await store.use_invite(digest(token), user["user_id"], _now_iso()):
            raise HTTPException(404, "this invitation has expired, was used, or was withdrawn")
        role = await store.role_in_org(user["user_id"], record["org_id"])
        if role is None:
            role = record["role"]
            await store.add_membership(user["user_id"], record["org_id"], role, _now_iso())
        org = await store.get_org(record["org_id"])
        return await _issue_session(user, record["org_id"], role,
                                    org["tier"] if org else "community")

    @app.post("/v1/invites/{token}/accept")
    async def accept_invite(token: str, caller: Claims = Depends(_caller)) -> dict:
        """Join with an account one already has; the answer is a session in
        the organization joined."""
        record = await _open_invite(token)
        user = await store.get_user(caller.user_id)
        if user is None:
            raise HTTPException(404, "user not found")
        return await _join(record, token, user)

    async def _signup_invited(body: SignupBody) -> dict:
        """A new account that joins the inviting organization — allowed
        whatever the signup mode: an admin chose to let this one in."""
        record = await _open_invite(body.invite or "")
        email = _norm_email(body.email)
        if record["email"] and record["email"] != email:
            raise HTTPException(403, "this invitation is for another address")
        if await store.get_user_by_email(email):
            raise HTTPException(409, "email already registered — sign in, then open the link")
        user_id = "usr_" + secrets.token_hex(8)
        await store.create_user(user_id, email, hash_password(body.password), _now_iso())
        user = {"user_id": user_id, "email": email}
        await _send_link(user, "verify", body.return_to, quiet=True)
        return await _join(record, body.invite or "", user)

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
        await _sso_enforced(profile.email,
                            await store.get_user_by_email(profile.email))
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

    # ── single sign-on (sso.py) ───────────────────────────────────────────────
    # A workspace's admin registers this service with their OpenID provider and
    # names the domains it signs in for; then a work address signs in there.
    sso_callback = f"{public_url}/v1/sso/callback" if public_url else ""

    def _http() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=oauth_transport, timeout=15)

    async def _sso_enforced(email: str, user: dict | None) -> None:
        """A domain whose workspace signs in only with SSO: no password, no
        GitHub / Google for its people. The workspace's owner keeps their
        password — the way back in if the provider is misconfigured."""
        held = await store.sso_for_domain(sso.domain_of(email))
        if held is None or not held["enforce"]:
            return
        if user is not None and await store.role_in_org(user["user_id"], held["org_id"]) == "owner":
            return
        org = await store.get_org(held["org_id"])
        raise HTTPException(403, {"code": "sso_required",
                                  "detail": f"{(org or {}).get('name', 'your workspace')} signs in "
                                            f"with single sign-on — use “Sign in with SSO”",
                                  "start": f"/v1/sso/start?email={email}"})

    def _sso_admin(org_id: str, caller: Claims) -> None:
        if caller.org != org_id or not role_at_least(caller.role, "admin"):
            raise HTTPException(403, "admin of this organization required")

    async def _provable_domains(org_id: str) -> set[str]:
        """The domains this workspace may claim: those of its owners' and
        admins' own addresses, verified — and no public mailbox domain."""
        out = set()
        for m in await store.org_members(org_id):
            if m["role"] not in ("owner", "admin"):
                continue
            user = await store.get_user(m["user_id"])
            if user and user["email_verified"]:
                out.add(sso.domain_of(user["email"]))
        return out - sso.PUBLIC_DOMAINS

    def _sso_view(org_id: str, held: dict | None, provable: set[str]) -> dict:
        base = {"available": bool(public_url), "callback_url": sso_callback,
                "provable_domains": sorted(provable)}
        if held is None:
            return {"configured": False, **base}
        return {"configured": True, "issuer": held["issuer"], "client_id": held["client_id"],
                "has_secret": bool(held["client_secret"]), "domains": held["domains"],
                "enforce": held["enforce"], "default_role": held["default_role"],
                "updated_by": held["updated_by"], "updated_ts": held["updated_ts"], **base}

    @app.get("/v1/orgs/{org_id}/sso")
    async def get_sso(org_id: str, caller: Claims = Depends(_caller)) -> dict:
        _sso_admin(org_id, caller)
        return _sso_view(org_id, await store.get_sso(org_id), await _provable_domains(org_id))

    @app.put("/v1/orgs/{org_id}/sso")
    async def put_sso(org_id: str, body: SSOBody, caller: Claims = Depends(_caller)) -> dict:
        _sso_admin(org_id, caller)
        if not public_url:
            raise HTTPException(409, "single sign-on needs this service's public address "
                                     "(AXOR_IDENTITY_PUBLIC_URL) — the provider sends "
                                     "people back to it")
        if body.default_role not in sso.DEFAULT_ROLES:
            raise HTTPException(422, f"default_role is one of {list(sso.DEFAULT_ROLES)}")
        held = await store.get_sso(org_id)
        secret = body.client_secret or (held or {}).get("client_secret")
        if not secret:
            raise HTTPException(422, "the client secret the provider gave this app is needed")
        domains = sorted({d.strip().lower().lstrip("@") for d in body.domains if d.strip()})
        provable = await _provable_domains(org_id)
        for d in domains:
            if d in sso.PUBLIC_DOMAINS:
                raise HTTPException(422, f"{d} is a public mailbox domain — nobody's to sign in")
            if d not in provable:
                raise HTTPException(422, {"code": "domain_unproven", "domain": d,
                                          "detail": f"{d} is not the domain of an owner's or "
                                                    f"admin's own confirmed address here — one "
                                                    f"of them confirms an address at {d} first"})
            other = await store.sso_for_domain(d)
            if other is not None and other["org_id"] != org_id:
                raise HTTPException(409, f"{d} signs in to another workspace already")
        issuer = body.issuer.strip().rstrip("/")
        try:
            async with _http() as http:
                await sso.discover(http, issuer, fresh=True)
        except sso.SSOError as exc:
            raise HTTPException(422, str(exc)) from exc
        await store.put_sso(org_id, {"issuer": issuer, "client_id": body.client_id.strip(),
                                     "client_secret": secret, "domains": domains,
                                     "enforce": body.enforce, "default_role": body.default_role,
                                     "updated_by": caller.email or caller.user_id,
                                     "updated_ts": _now_iso()})
        return _sso_view(org_id, await store.get_sso(org_id), provable)

    @app.delete("/v1/orgs/{org_id}/sso", status_code=204)
    async def delete_sso(org_id: str, caller: Claims = Depends(_caller)) -> None:
        _sso_admin(org_id, caller)
        await store.delete_sso(org_id)

    @app.get("/v1/sso/discover")
    async def sso_discover(email: str = "") -> dict:
        """Whether a work address signs in with SSO — what the login form asks
        before it shows the button. Says the workspace's name and nothing else."""
        held = await store.sso_for_domain(sso.domain_of(_norm_email(email))) \
            if public_url and "@" in email else None
        if held is None:
            return {"sso": False}
        org = await store.get_org(held["org_id"])
        return {"sso": True, "workspace": (org or {}).get("name", ""), "enforce": held["enforce"]}

    @app.get("/v1/sso/start")
    async def sso_start(email: str = "", org: str = "", return_to: str = "/") -> RedirectResponse:
        target = _safe_return(return_to)
        if target is None:
            raise HTTPException(400, "return_to is not an address this service sends logins to")
        email = _norm_email(email)
        held = await store.get_sso(org) if org else await store.sso_for_domain(sso.domain_of(email))
        if held is None or not public_url:
            return RedirectResponse(_with_query(
                target, axor_login_error="no single sign-on for that address — sign in "
                                         "with your password, or ask your workspace's admin"), 302)
        cfg = sso.Config.of(held)
        try:
            async with _http() as http:
                doc = await sso.discover(http, cfg.issuer)
        except sso.SSOError as exc:
            return RedirectResponse(_with_query(target, axor_login_error=str(exc)), 302)
        await store.sweep_expired(_now_iso())
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        await store.put_state(digest(state), sso.PREFIX + cfg.org_id, verifier, target,
                              _expiry(_STATE_TTL))
        return RedirectResponse(sso.authorize_url(
            doc, cfg, redirect_uri=sso_callback, state=state, challenge=challenge,
            nonce=sso.nonce_for(verifier), login_hint=email), 302)

    @app.get("/v1/sso/callback")
    async def sso_callback_route(state: str = "", code: str = "", error: str = "",
                                 error_description: str = "") -> RedirectResponse:
        flight = await store.take_state(digest(state)) if state else None
        if flight is None or not flight["provider"].startswith(sso.PREFIX) \
                or flight["expires_ts"] <= _now_iso():
            raise HTTPException(400, "this sign-in link has expired — start again")
        back = flight["return_to"]
        org_id = flight["provider"][len(sso.PREFIX):]
        held = await store.get_sso(org_id)
        if held is None:
            return RedirectResponse(_with_query(
                back, axor_login_error="single sign-on was turned off for this workspace"), 302)
        if error or not code:
            return RedirectResponse(_with_query(
                back, axor_login_error=f"single sign-on was cancelled"
                                       f"{': ' + error_description if error_description else ''}"),
                302)
        cfg = sso.Config.of(held)
        try:
            async with _http() as http:
                doc = await sso.discover(http, cfg.issuer)
                token = await sso.exchange(http, doc, cfg, code=code, redirect_uri=sso_callback,
                                           verifier=flight["verifier"])
                claims = await sso.verify(http, doc, cfg, token,
                                          nonce=sso.nonce_for(flight["verifier"]))
            user_id, created = await _sso_account(cfg, claims)
        except sso.SSOError as exc:
            return RedirectResponse(_with_query(back, axor_login_error=str(exc)), 302)
        except HTTPException as exc:
            return RedirectResponse(_with_query(back, axor_login_error=str(exc.detail)), 302)
        raw = secrets.token_urlsafe(32)
        await store.put_login_code(digest(raw), user_id, created, _expiry(_CODE_TTL))
        return RedirectResponse(_with_query(back, axor_login=raw, axor_org=org_id), 302)

    async def _sso_account(cfg: sso.Config, claims: dict) -> tuple[str, bool]:
        """The account an SSO sign-in is, and its place in the workspace.

        1. The provider's subject linked before → that account.
        2. An account with this address → linked to it: the workspace proved
           the domain (sso.py), so its provider speaks for its addresses.
        3. Neither → a new account, its address vouched for by the provider.

        Then a membership with the workspace's default role if there is none:
        the provider says who works there. Never more than the default — a
        role above it is the workspace admin's to give."""
        ts = _now_iso()
        provider = sso.PREFIX + cfg.org_id
        subject = str(claims["sub"])[:128]
        email = claims["email"]
        created = False
        user_id = await store.user_for_identity(provider, subject)
        if user_id is None:
            existing = await store.get_user_by_email(email)
            if existing is not None:
                user_id = existing["user_id"]
                if not existing["email_verified"]:
                    await store.mark_verified(
                        user_id, drop_password=bool(existing["password_hash"]))
                    await store.revoke_all_refresh(user_id)
            else:
                user_id = "usr_" + secrets.token_hex(8)
                await store.create_user(user_id, email, None, ts, email_verified=True)
                created = True
            await store.link_identity(provider, subject, user_id, email, ts)
        if await store.role_in_org(user_id, cfg.org_id) is None:
            await store.add_membership(user_id, cfg.org_id, cfg.default_role, ts)
        return user_id, created

    # ── e-mail: verification and password reset ───────────────────────────────
    def _expiry(seconds: int) -> str:
        return (datetime.datetime.now(datetime.UTC)
                + datetime.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def _send_link(user: dict, purpose: str, return_to: str | None, *,
                         quiet: bool = False) -> bool:
        """Mail ``user`` a single-use link. ``quiet``: a failure is logged, not
        raised (a signup does not fail because the mail server is down).
        Returns whether a message went out."""
        if mail is None:
            return False
        key = f"{purpose}:{user['user_id']}"
        now = datetime.datetime.now(datetime.UTC).timestamp()
        if now - last_mail.get(key, 0) < _MAIL_GAP:
            if quiet:
                return False
            raise HTTPException(429, "a link was just sent — check your inbox, "
                                     "or try again in a minute")
        target = _safe_return(return_to or "/") if public_url else None
        if target is None:
            if quiet:
                return False
            raise HTTPException(400, "return_to is not an address this service links to")
        raw = secrets.token_urlsafe(32)
        ttl = _VERIFY_TTL if purpose == "verify" else _RESET_TTL
        await store.put_email_token(digest(raw), user["user_id"], purpose,
                                    user["email"], _expiry(ttl))
        param = "axor_verify" if purpose == "verify" else "axor_reset"
        link = _with_query(target, **{param: raw})
        if purpose == "verify":
            subject = "Confirm your e-mail address for Axor"
            text = ("Confirm that this address is yours:\n\n" + link +
                    "\n\nThe link works once, for 24 hours. If you did not create "
                    "an Axor account, ignore this message.")
        else:
            subject = "Reset your Axor password"
            text = ("Someone asked to reset the password of the Axor account for "
                    "this address. To choose a new one:\n\n" + link +
                    "\n\nThe link works once, for an hour. If it was not you, "
                    "ignore this message — the password stays as it is.")
        try:
            await mail.send(user["email"], subject, text)
        except Exception as exc:  # noqa: BLE001 - any transport failure
            if quiet:
                logging.getLogger("axor_identity.mail").warning(
                    "could not send %s mail: %s", purpose, exc)
                return False
            raise HTTPException(502, "the mail server did not take the message") from exc
        last_mail[key] = now
        return True

    @app.get("/v1/email")
    async def email_doc() -> dict:
        """Whether this install sends mail, so a client offers "forgot
        password?" and "resend" only where they can work."""
        return {"available": mail is not None and bool(public_url)}

    @app.post("/v1/email/verify/send", status_code=202)
    async def verify_send(body: LinkBody, caller: Claims = Depends(_caller)) -> dict:
        if mail is None or not public_url:
            raise HTTPException(503, "this installation does not send mail")
        user = await store.get_user(caller.user_id)
        if user is None:
            raise HTTPException(404, "user not found")
        if user["email_verified"]:
            raise HTTPException(409, "this address is already verified")
        await _send_link(user, "verify", body.return_to)
        return {"sent_to": user["email"]}

    @app.post("/v1/email/verify")
    async def verify(body: TokenBody) -> dict:
        record = await store.take_email_token(digest(body.token), "verify")
        user = await store.get_user(record["user_id"]) if record else None
        if record is None or user is None or record["expires_ts"] <= _now_iso() \
                or record["email"] != user["email"]:
            raise HTTPException(400, "this link has expired or was already used")
        await store.mark_verified(user["user_id"])
        return {"email": user["email"], "email_verified": True}

    @app.post("/v1/password/forgot", status_code=202)
    async def forgot(body: ForgotBody) -> dict:
        """Always the same answer, whether or not the address has an account —
        the form must not tell a stranger who is registered."""
        if mail is None or not public_url:
            raise HTTPException(503, "this installation does not send mail")
        if _safe_return(body.return_to or "/") is None:
            raise HTTPException(400, "return_to is not an address this service links to")
        user = await store.get_user_by_email(_norm_email(body.email))
        if user is not None:
            await _send_link(user, "reset", body.return_to, quiet=True)
        return {"detail": "if that address has an account, a link is on its way"}

    @app.post("/v1/password/reset", status_code=204)
    async def reset(body: ResetBody) -> None:
        """A new password from an e-mailed link. Following the link proves the
        address, so it is marked verified; every session open before the reset
        is revoked — a reset is often someone taking their account back."""
        record = await store.take_email_token(digest(body.token), "reset")
        user = await store.get_user(record["user_id"]) if record else None
        if record is None or user is None or record["expires_ts"] <= _now_iso() \
                or record["email"] != user["email"]:
            raise HTTPException(400, "this link has expired or was already used")
        await store.update_password(user["user_id"], hash_password(body.password))
        await store.mark_verified(user["user_id"])
        await store.revoke_all_refresh(user["user_id"])

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
