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
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, EmailStr, Field

from axor_identity import licensing, saml, sso
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


def _hours_after(ts: str, hours: int) -> str:
    start = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return (start + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    protocol: str = "oidc"                 # oidc | saml
    # OpenID Connect: the provider and this app's client there
    issuer: str = Field(default="", max_length=500)
    client_id: str = Field(default="", max_length=300)
    client_secret: str | None = Field(default=None, max_length=500)  # kept if left out
    # SAML: the provider's metadata — or its SSO URL, entity id and certificate
    saml_metadata: str | None = Field(default=None, max_length=200_000)
    saml_sso_url: str = Field(default="", max_length=1000)
    saml_entity_id: str = Field(default="", max_length=500)
    saml_cert: str = Field(default="", max_length=40_000)  # one PEM, or two in rotation
    saml_sign_requests: bool = False      # the provider wants our requests signed (ADFS)
    saml_idp_initiated: bool = False      # take sign-ins started on the provider's dashboard
    domains: list[str] = Field(min_length=1, max_length=20)
    enforce: bool = False
    default_role: str = "member"


class LicenseBody(BaseModel):
    license_json: str = Field(min_length=10, max_length=20_000)


class SessionPolicyBody(BaseModel):
    # sign in again after this many hours (1 hour to 90 days); null: no limit
    # beyond the deployment's own
    hours: int | None = Field(default=None, ge=1, le=24 * 90)


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
               vendor_pubkey: str | None = None,
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

    # the vendor's key licenses are signed with (the control plane pins the same)
    vendor_pubkey = (vendor_pubkey if vendor_pubkey is not None
                     else os.environ.get("AXOR_VENDOR_PUBKEY", "")).strip()
    org_pin = (os.environ.get("AXOR_ORG") or "").strip()

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

    async def _issue_session(user: dict, org_id: str, role: str, tier: str,
                             session_ts: str | None = None) -> dict:
        raw_refresh, token_hash = new_refresh()
        now = datetime.datetime.now(datetime.UTC)
        expires = (now + datetime.timedelta(seconds=refresh_ttl)).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        await store.store_refresh(token_hash, user["user_id"], org_id, expires,
                                  _now_iso(), session_ts=session_ts)
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
        # Log-in and "forgot password" do not say whether an address has an
        # account; sign-up does, deliberately (QA-048). It signs the new account
        # in at once, so an answer that looked the same as success would need
        # sign-up to wait for a mailed link first — and an install without mail
        # could not sign anyone up. So it says so, and what to do instead.
        if await store.get_user_by_email(email):
            raise HTTPException(409, "that address already has an account — log in, "
                                     "or reset the password if you forgot it")
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
        hours = org.get("session_hours")
        if hours and _now_iso() >= _hours_after(record["session_ts"], hours):
            # the workspace's policy: a sign-in lasts so long, refreshed or not
            raise HTTPException(401, f"{org['name']} asks everyone to sign in again "
                                     f"every {hours} hour{'s' if hours != 1 else ''}")
        return await _issue_session(user, org["org_id"], role, org["tier"],
                                    session_ts=record["session_ts"])

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
        await _on_trail(org_id, "member_added", target["email"], who=_who(caller),
                        role=body.role)
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
        before = await store.role_in_org(user_id, org_id)
        await store.set_member_role(user_id, org_id, body.role)
        if before != body.role:
            await _on_trail(org_id, "member_role", await _email_of(user_id), who=_who(caller),
                            **{"from": before or "", "to": body.role})
        return {"user_id": user_id, "org_id": org_id, "role": body.role}

    @app.delete("/v1/orgs/{org_id}/members/{user_id}")
    async def remove_member(org_id: str, user_id: str,
                            caller: Claims = Depends(_caller)) -> dict:
        await _changeable(org_id, user_id, caller)
        await store.remove_member(user_id, org_id)
        await store.revoke_refresh_in_org(user_id, org_id)
        await _on_trail(org_id, "member_removed", await _email_of(user_id), who=_who(caller))
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
        await _on_trail(org_id, "invite_sent", email or "an open link", who=_who(caller),
                        role=body.role)
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
        await _on_trail(org_id, "invite_withdrawn", invite_id, who=_who(caller))

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
            await _on_trail(record["org_id"], "member_added", user["email"], who=user["email"],
                            role=role, invited=True)
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

    def _who(caller: Claims) -> str:
        return caller.email or caller.user_id

    async def _email_of(user_id: str) -> str:
        user = await store.get_user(user_id)
        return user["email"] if user else user_id

    def _plane_bearer(org_id: str) -> str:
        """This service to the control plane, for one workspace: a two-minute
        token signed with the key the plane already trusts, as its admin."""
        return issue_access(signing_key, user_id="svc_identity", email="identity@axor",
                            org=org_id, role="admin", tier="community", ttl=120)

    async def _on_trail(org_id: str, action: str, target: str = "", *, who: str,
                        **detail: object) -> None:
        """Tell the workspace's audit trail (on the control plane) what changed
        among its people, its sign-in or its sessions. This service keeps the
        people; the plane keeps the record. Never blocks the act: a plane that
        is down costs the line, logged."""
        if not plane_url:
            return
        try:
            async with _http() as http:
                r = await http.post(f"{plane_url}/v1/audit/identity",
                                    json={"action": action, "target": target, "who": who,
                                          "detail": detail},
                                    headers={"Authorization": f"Bearer {_plane_bearer(org_id)}"})
            if r.status_code >= 400:
                logging.getLogger("axor_identity.audit").warning(
                    "the control plane did not record %s: %s %s", action, r.status_code,
                    r.text[:200])
        except httpx.HTTPError as exc:
            logging.getLogger("axor_identity.audit").warning(
                "could not tell the control plane about %s: %s", action, exc)

    # ── sessions: how long a sign-in lasts, and ending them all ──────────────
    @app.get("/v1/orgs/{org_id}/sessions")
    async def get_session_policy(org_id: str, caller: Claims = Depends(_caller)) -> dict:
        _sso_admin(org_id, caller)
        org = await store.get_org(org_id)
        return {"hours": (org or {}).get("session_hours"),
                "deployment_hours": refresh_ttl // 3600, "access_minutes": access_ttl // 60,
                "plan_ok": await _on_business(org_id)}

    @app.put("/v1/orgs/{org_id}/sessions")
    async def put_session_policy(org_id: str, body: SessionPolicyBody,
                                 caller: Claims = Depends(_caller)) -> dict:
        """Everyone signs in again after ``hours`` — counted from the sign-in,
        however often the session refreshed. Business: a company's policy."""
        _sso_admin(org_id, caller)
        if not await _on_business(org_id):
            raise HTTPException(402, {"code": "plan_required", "plan": "business",
                                      "detail": "a session limit for the workspace is on the "
                                                "Business plan"})
        await store.set_session_hours(org_id, body.hours)
        await _on_trail(org_id, "sessions_limit", f"{body.hours} h" if body.hours else "none",
                        who=_who(caller), hours=body.hours or 0)
        return await get_session_policy(org_id, caller)

    @app.post("/v1/orgs/{org_id}/sessions/end")
    async def end_all_sessions(org_id: str, caller: Claims = Depends(_caller)) -> dict:
        """Everyone signs in again — but the admin who asked. On every plan: a
        stolen laptop is not a premium problem. A token already handed out runs
        out within its few minutes; nothing renews it."""
        _sso_admin(org_id, caller)
        ended = await store.end_sessions_in_org(org_id, keep_user=caller.user_id)
        await _on_trail(org_id, "sessions_ended", f"{ended} people", who=_who(caller),
                        ended=ended)
        return {"ended": ended, "access_minutes": access_ttl // 60}

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

    # A licence the operator gave the deployment (AXOR_LICENSE), as the plane
    # takes it: it covers the workspace it is issued to — every workspace on a
    # single-company install pinned with AXOR_ORG to the licence's organization.
    env_license: licensing.License | None = None
    raw_env_license = (os.environ.get("AXOR_LICENSE") or "").strip()
    if raw_env_license and vendor_pubkey:
        try:
            env_license = licensing.verify(raw_env_license, vendor_pubkey)
        except licensing.LicenseError as exc:
            logging.getLogger("axor_identity").warning("AXOR_LICENSE ignored: %s", exc)

    async def _on_business(org_id: str) -> bool:
        today = _now_iso()[:10]
        if env_license and env_license.organization in (org_id, org_pin or org_id) \
                and licensing.at_least(env_license.tier, env_license.expires_at, today,
                                       "business"):
            return True
        org = await store.get_org(org_id)
        return bool(org) and licensing.at_least(org["tier"], org.get("tier_expires"),
                                                today, "business")

    @app.put("/v1/orgs/{org_id}/license")
    async def put_license(org_id: str, body: LicenseBody,
                          caller: Claims = Depends(_caller)) -> dict:
        """The workspace's plan, from the license its owner activated on the
        control plane — the same vendor-signed file, checked here against the
        same pinned key, for this workspace, in date."""
        _sso_admin(org_id, caller)
        if not vendor_pubkey:
            raise HTTPException(409, "this identity service has no vendor key pinned "
                                     "(AXOR_VENDOR_PUBKEY) to check a license with")
        try:
            lic = licensing.verify(body.license_json, vendor_pubkey)
        except licensing.LicenseError as exc:
            raise HTTPException(403, str(exc)) from exc
        if lic.organization not in (org_id, org_pin or org_id):
            raise HTTPException(403, f"this license is issued to {lic.organization!r}, "
                                     f"not this workspace")
        if _now_iso()[:10] > lic.expires_at:
            raise HTTPException(403, f"this license expired on {lic.expires_at}")
        await store.set_org_tier(org_id, lic.tier, lic.expires_at)
        await _on_trail(org_id, "plan_activated", lic.tier, who=_who(caller),
                        tier=lic.tier, until=lic.expires_at)
        return {"org_id": org_id, "tier": lic.tier, "expires_at": lic.expires_at}

    @app.post("/v1/orgs/{org_id}/license/renewal")
    async def renew_license(org_id: str, body: LicenseBody) -> dict:
        """The next license, from the control plane once it renewed one — with
        no caller at all. The control plane renews on its own schedule and holds
        no admin's session to paste it with; without this the plan here stays
        at the old date and single sign-on lapses on a paid workspace.

        Safe without a caller for the reason the plane's own renewal is: the
        file is vendor-signed, issued to this workspace and in date, and it
        must end LATER than the plan in force — so the only thing a caller can
        do is move a workspace forward to a plan the vendor issued it."""
        if not vendor_pubkey:
            raise HTTPException(409, "this identity service has no vendor key pinned "
                                     "(AXOR_VENDOR_PUBKEY) to check a license with")
        org = await store.get_org(org_id)
        if org is None:
            raise HTTPException(404, "no such organization")
        try:
            lic = licensing.verify(body.license_json, vendor_pubkey)
        except licensing.LicenseError as exc:
            raise HTTPException(403, str(exc)) from exc
        if lic.organization not in (org_id, org_pin or org_id):
            raise HTTPException(403, f"this license is issued to {lic.organization!r}, "
                                     f"not this workspace")
        if _now_iso()[:10] > lic.expires_at:
            raise HTTPException(403, f"this license expired on {lic.expires_at}")
        held = org.get("tier_expires")
        # Forward in time, or up the ladder within the same period: an upgrade
        # bought mid-period is issued to the same end date, and refusing it
        # left identity on the old plan — Business paid for, single sign-on and
        # SCIM still locked (QA-130). An older, lower license replayed at the
        # same date is still refused.
        upgrade = (held and lic.expires_at == held
                   and licensing.rank(lic.tier) > licensing.rank(str(org.get("tier") or "")))
        if held and lic.expires_at <= held and not upgrade:
            raise HTTPException(409, f"this license ends on {lic.expires_at}, not after "
                                     f"the plan in force ({held}), and is not a higher "
                                     "plan — renewal only moves forward")
        await store.set_org_tier(org_id, lic.tier, lic.expires_at)
        await _on_trail(org_id, "plan_upgraded" if upgrade else "plan_renewed", lic.tier,
                        who="license renewal", tier=lic.tier, until=lic.expires_at)
        return {"org_id": org_id, "tier": lic.tier, "expires_at": lic.expires_at}

    def _sso_view(org_id: str, held: dict | None, provable: set[str],
                  plan_ok: bool = True) -> dict:
        base = {"available": bool(public_url), "callback_url": sso_callback,
                "provable_domains": sorted(provable), "plan_ok": plan_ok}
        base |= {"saml_entity_id_ours": f"{public_url}/v1/saml/{org_id}",
                 "saml_acs_url": f"{public_url}/v1/saml/acs",
                 "saml_metadata_url": f"{public_url}/v1/saml/{org_id}/metadata",
                 "saml_cert_url": f"{public_url}/v1/saml/{org_id}/certificate"}
        if held is None:
            return {"configured": False, **base}
        return {"configured": True, "protocol": held.get("protocol", "oidc"),
                "saml_sso_url": held.get("saml_sso_url", ""),
                "saml_entity_id": held.get("saml_entity_id", ""),
                "has_cert": bool(held.get("saml_cert")),
                "certs": len(saml.IdP("", "", held.get("saml_cert", "")).certs),
                "saml_sign_requests": held.get("saml_sign_requests", False),
                "saml_idp_initiated": held.get("saml_idp_initiated", False),
                "issuer": held["issuer"], "client_id": held["client_id"],
                "has_secret": bool(held["client_secret"]), "domains": held["domains"],
                "enforce": held["enforce"], "default_role": held["default_role"],
                "updated_by": held["updated_by"], "updated_ts": held["updated_ts"], **base}

    @app.get("/v1/orgs/{org_id}/sso")
    async def get_sso(org_id: str, caller: Claims = Depends(_caller)) -> dict:
        _sso_admin(org_id, caller)
        return _sso_view(org_id, await store.get_sso(org_id), await _provable_domains(org_id),
                         await _on_business(org_id))

    @app.put("/v1/orgs/{org_id}/sso")
    async def put_sso(org_id: str, body: SSOBody, caller: Claims = Depends(_caller)) -> dict:
        _sso_admin(org_id, caller)
        if not public_url:
            raise HTTPException(409, "single sign-on needs this service's public address "
                                     "(AXOR_IDENTITY_PUBLIC_URL) — the provider sends "
                                     "people back to it")
        if not await _on_business(org_id):
            raise HTTPException(402, {"code": "plan_required", "plan": "business",
                                      "detail": "single sign-on is on the Business plan "
                                                "($40 per developer) — see Pricing"})
        if body.default_role not in sso.DEFAULT_ROLES:
            raise HTTPException(422, f"default_role is one of {list(sso.DEFAULT_ROLES)}")
        if body.protocol not in ("oidc", "saml"):
            raise HTTPException(422, "protocol is oidc or saml")
        held = await store.get_sso(org_id)
        secret = body.client_secret or (held or {}).get("client_secret") or ""
        if body.protocol == "oidc" and not secret:
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
        common = {"domains": domains, "enforce": body.enforce,
                  "default_role": body.default_role, "protocol": body.protocol,
                  "updated_by": caller.email or caller.user_id, "updated_ts": _now_iso()}
        if body.protocol == "saml":
            try:
                if body.saml_metadata:
                    idp = saml.from_metadata(body.saml_metadata)
                else:
                    cert = body.saml_cert or (held or {}).get("saml_cert") or ""
                    if not body.saml_sso_url.startswith(("https://", "http://localhost")) \
                            or not cert:
                        raise saml.SAMLError("give the provider's metadata, or its single "
                                             "sign-on URL (https) and its certificate")
                    idp = saml.IdP(body.saml_sso_url.strip(), body.saml_entity_id.strip(),
                                   saml.normalize_certs(cert))
            except saml.SAMLError as exc:
                raise HTTPException(422, str(exc)) from exc
            await store.put_sso(org_id, {**common, "issuer": "", "client_id": "",
                                         "client_secret": "", "saml_sso_url": idp.sso_url,
                                         "saml_entity_id": idp.entity_id,
                                         "saml_cert": idp.cert_pem,
                                         "saml_sign_requests": body.saml_sign_requests,
                                         "saml_idp_initiated": body.saml_idp_initiated})
            await _on_trail(org_id, "sso_set", idp.entity_id or idp.sso_url, who=_who(caller),
                            protocol="saml", domains=domains, enforce=body.enforce,
                            default_role=body.default_role)
            return _sso_view(org_id, await store.get_sso(org_id), provable)
        issuer = body.issuer.strip().rstrip("/")
        if len(issuer) < 8 or not body.client_id.strip():
            raise HTTPException(422, "the provider's issuer and this app's client id are needed")
        try:
            async with _http() as http:
                await sso.discover(http, issuer, fresh=True)
        except sso.SSOError as exc:
            raise HTTPException(422, str(exc)) from exc
        await store.put_sso(org_id, {**common, "issuer": issuer,
                                     "client_id": body.client_id.strip(),
                                     "client_secret": secret, "saml_sso_url": "",
                                     "saml_entity_id": "", "saml_cert": "",
                                     "saml_sign_requests": False, "saml_idp_initiated": False})
        await _on_trail(org_id, "sso_set", issuer, who=_who(caller), protocol="oidc",
                        domains=domains, enforce=body.enforce, default_role=body.default_role)
        return _sso_view(org_id, await store.get_sso(org_id), provable)

    @app.delete("/v1/orgs/{org_id}/sso", status_code=204)
    async def delete_sso(org_id: str, caller: Claims = Depends(_caller)) -> None:
        _sso_admin(org_id, caller)
        if await store.get_sso(org_id) is not None:
            await store.delete_sso(org_id)
            await _on_trail(org_id, "sso_removed", who=_who(caller))

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
        if held.get("protocol") == "saml":
            await store.sweep_expired(_now_iso())
            relay = secrets.token_urlsafe(32)
            sp = await _sp_key(held["org_id"]) if held.get("saml_sign_requests") else None
            url, request_id = saml.authn_request(
                saml.IdP(held["saml_sso_url"], held["saml_entity_id"], held["saml_cert"]),
                entity_id=f"{public_url}/v1/saml/{held['org_id']}",
                acs_url=f"{public_url}/v1/saml/acs", relay_state=relay,
                sign_key_pem=sp["key_pem"] if sp else None)
            await store.put_state(digest(relay), saml.PREFIX + held["org_id"], request_id,
                                  target, _expiry(_STATE_TTL))
            return RedirectResponse(url, 302)
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

    async def _sp_key(org_id: str) -> dict:
        """The workspace's own SAML key pair, made the first time it is asked
        for (its metadata, a signed request, an encrypted assertion)."""
        held = await store.sp_key(org_id)
        if held is None:
            key_pem, cert_pem = saml.new_sp_key(f"{public_url}/v1/saml/{org_id}")
            await store.put_sp_key(org_id, key_pem, cert_pem, _now_iso())
            held = await store.sp_key(org_id)
        return held or {}

    async def _known_org(org_id: str) -> None:
        if not public_url:
            raise HTTPException(404, "no public address set")
        if await store.get_org(org_id) is None:
            raise HTTPException(404, "no such workspace")

    @app.get("/v1/saml/{org_id}/metadata")
    async def saml_metadata(org_id: str) -> Response:
        """This service as a SAML service provider for the workspace — what its
        admin gives the identity provider: entity id, ACS URL, our
        certificate (to check our requests and encrypt to us)."""
        await _known_org(org_id)
        held = await store.get_sso(org_id) or {}
        sp = await _sp_key(org_id)
        return Response(saml.sp_metadata(f"{public_url}/v1/saml/{org_id}",
                                         f"{public_url}/v1/saml/acs", sp.get("cert_pem"),
                                         sign_requests=bool(held.get("saml_sign_requests"))),
                        media_type="application/samlmetadata+xml")

    @app.get("/v1/saml/{org_id}/certificate")
    async def saml_certificate(org_id: str) -> Response:
        """Our certificate alone, for a provider that takes a file (Entra ID's
        verification certificate, ADFS's encryption tab)."""
        await _known_org(org_id)
        return Response((await _sp_key(org_id))["cert_pem"], media_type="application/x-pem-file",
                        headers={"Content-Disposition": 'attachment; filename="axor-saml.pem"'})

    @app.post("/v1/saml/acs")
    async def saml_acs(request: Request) -> RedirectResponse:
        """The provider posts its SAMLResponse here (HTTP-POST binding)."""
        from urllib.parse import parse_qs

        form = parse_qs((await request.body()).decode(errors="replace"))
        relay = (form.get("RelayState") or [""])[0]
        answer = (form.get("SAMLResponse") or [""])[0]
        flight = await store.take_state(digest(relay)) if relay else None
        if flight is not None and flight["provider"].startswith(saml.PREFIX) \
                and flight["expires_ts"] > _now_iso():
            # we asked: the answer must be to that request
            back, request_id = flight["return_to"], flight["verifier"]
            org_id = flight["provider"][len(saml.PREFIX):]
        else:
            # nobody here asked: a sign-in started on the provider's dashboard,
            # taken only where the workspace said so
            org_id = saml.audience_org(answer, f"{public_url}/v1/saml/") or ""
            started = await store.get_sso(org_id) if org_id else None
            if not (started and started.get("protocol") == "saml"
                    and started.get("saml_idp_initiated")):
                raise HTTPException(400, "this sign-in has expired, or was not started here — "
                                         "start again from the login form")
            back = _safe_return(relay if relay.startswith("/") else "/") or _safe_return("/")
            request_id = None
        held = await store.get_sso(org_id)
        if held is None or held.get("protocol") != "saml":
            return RedirectResponse(_with_query(
                back, axor_login_error="single sign-on was changed for this workspace — "
                                       "start again"), 303)
        try:
            claims = saml.read_response(
                answer,
                saml.IdP(held["saml_sso_url"], held["saml_entity_id"], held["saml_cert"]),
                entity_id=f"{public_url}/v1/saml/{org_id}", acs_url=f"{public_url}/v1/saml/acs",
                request_id=request_id, domains=tuple(held["domains"]),
                sp_key_pem=(await _sp_key(org_id)).get("key_pem"))
            if request_id is None:
                await store.sweep_expired(_now_iso())
                if not claims["assertion_id"] or not await store.first_use(
                        digest(f"saml-assertion:{org_id}:{claims['assertion_id']}"),
                        claims["until"]):
                    raise saml.SAMLError("this sign-in was used already — start again "
                                         "from your provider")
            user_id, created = await _sso_account(sso.Config.of(held), claims)
        except saml.SAMLError as exc:
            return RedirectResponse(_with_query(back, axor_login_error=str(exc)), 303)
        except HTTPException as exc:
            return RedirectResponse(_with_query(back, axor_login_error=str(exc.detail)), 303)
        raw = secrets.token_urlsafe(32)
        await store.put_login_code(digest(raw), user_id, created, _expiry(_CODE_TTL))
        # 303: the browser follows a POST's answer with a GET
        return RedirectResponse(_with_query(back, axor_login=raw, axor_org=org_id), 303)

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
            await _on_trail(cfg.org_id, "member_added", email, who=email,
                            role=cfg.default_role, via="single sign-on")
        return user_id, created

    # ── SCIM 2.0: people come and go with the workspace's identity provider ───
    # The provider holds a bearer token for one workspace and creates, updates
    # and deactivates its people here (RFC 7643/7644, the Users resource). A
    # person deactivated or deleted leaves the workspace: their membership goes,
    # their sessions in it end, and — when the control plane is wired here —
    # the keys their machines report with are revoked.
    scim_base = f"{public_url}/scim/v2" if public_url else ""
    plane_url = (os.environ.get("AXOR_IDENTITY_PLANE_URL") or "").strip().rstrip("/")
    plane_token = (os.environ.get("AXOR_IDENTITY_PLANE_TOKEN") or "").strip()
    _USER = "urn:ietf:params:scim:schemas:core:2.0:User"
    _LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
    _ERR = "urn:ietf:params:scim:api:messages:2.0:Error"
    _PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
    _SCIM = "application/scim+json"

    def _scim(body: dict, status: int = 200) -> JSONResponse:
        return JSONResponse(body, status_code=status, media_type=_SCIM)

    def _scim_error(status: int, detail: str, scim_type: str = "") -> JSONResponse:
        return _scim({"schemas": [_ERR], "status": str(status), "detail": detail,
                      **({"scimType": scim_type} if scim_type else {})}, status)

    class _ScimRefused(Exception):
        def __init__(self, status: int, detail: str, scim_type: str = "") -> None:
            self.status, self.detail, self.scim_type = status, detail, scim_type

    @app.exception_handler(_ScimRefused)
    async def _scim_refused(_request: Request, exc: _ScimRefused) -> JSONResponse:
        return _scim_error(exc.status, exc.detail, exc.scim_type)

    async def _scim_org(authorization: str | None = Header(default=None)) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise _ScimRefused(401, "a bearer token is required")
        org_id = await store.org_for_scim(digest(authorization.split(" ", 1)[1].strip()),
                                          _now_iso())
        if org_id is None:
            raise _ScimRefused(401, "this token provisions no workspace here")
        if not await _on_business(org_id):
            raise _ScimRefused(403, "provisioning is on the Business plan")
        return org_id

    _SCIM_WHO = "your identity provider (SCIM)"

    def _user_resource(user: dict, active: bool) -> dict:
        named = {"displayName": user["display_name"],
                 "name": {"formatted": user["display_name"]}} if user.get("display_name") else {}
        return {"schemas": [_USER], "id": user["user_id"], "userName": user["email"],
                **named,
                "active": active, "emails": [{"value": user["email"], "primary": True,
                                              "type": "work"}],
                "meta": {"resourceType": "User", "created": user.get("created_ts", ""),
                         "location": f"{scim_base}/Users/{user['user_id']}"}}

    async def _scim_domains(org_id: str) -> set[str]:
        held = await store.get_sso(org_id)
        return set(held["domains"]) if held else await _provable_domains(org_id)

    async def _deprovision(org_id: str, user_id: str) -> None:
        role = await store.role_in_org(user_id, org_id)
        if role is None:
            return
        if role == "owner":
            raise _ScimRefused(409, "the workspace's owner is not deprovisioned from the "
                                    "identity provider — they are its way back in", "mutability")
        await store.remove_member(user_id, org_id)
        await store.revoke_refresh_in_org(user_id, org_id)
        await _on_trail(org_id, "scim_removed", await _email_of(user_id), who=_SCIM_WHO)
        if plane_url:
            # the plane trusts this service's signature: a two-minute token for
            # this workspace, as its admin — no shared secret, any tenancy. An
            # operator token, if one is set, is used instead (an older plane).
            bearer = plane_token or _plane_bearer(org_id)
            try:
                async with _http() as http:
                    await http.delete(f"{plane_url}/v1/keys/of/{user_id}",
                                      headers={"Authorization": f"Bearer {bearer}"})
            except httpx.HTTPError as exc:
                logging.getLogger("axor_identity.scim").warning(
                    "could not revoke %s's keys on the control plane: %s", user_id, exc)

    async def _provision(org_id: str, user_id: str) -> None:
        if await store.role_in_org(user_id, org_id) is None:
            held = await store.get_sso(org_id)
            role = held["default_role"] if held else "member"
            await store.add_membership(user_id, org_id, role, _now_iso())
            await _on_trail(org_id, "scim_added", await _email_of(user_id), who=_SCIM_WHO,
                            role=role)

    def _profile_of(body: dict) -> dict:
        """What a SCIM body (a POST or PUT User, or a PATCH's operations,
        flattened) says about the person: ``email`` and ``name``."""
        flat: dict = {}
        if "Operations" in body:
            for op in body.get("Operations") or []:
                if not isinstance(op, dict) or str(op.get("op", "")).lower() not in (
                        "replace", "add"):
                    continue
                path, value = str(op.get("path") or ""), op.get("value")
                if path:
                    flat[path] = value
                elif isinstance(value, dict):
                    flat.update(value)
        else:
            flat = body
        out: dict = {}
        email = flat.get("userName")
        if not email:
            emails = flat.get("emails")
            if isinstance(emails, list):
                primary = [e for e in emails if isinstance(e, dict) and e.get("primary")] \
                    or [e for e in emails if isinstance(e, dict)]
                email = (primary[0].get("value") if primary else None)
        if not email:
            email = next((v for k, v in flat.items() if k.lower().startswith("emails[")
                          and k.lower().endswith(".value")), None)
        if isinstance(email, str) and "@" in email:
            out["email"] = _norm_email(email)
        name = flat.get("name") if isinstance(flat.get("name"), dict) else {}
        given = flat.get("name.givenName") or name.get("givenName")
        family = flat.get("name.familyName") or name.get("familyName")
        display = flat.get("displayName") or flat.get("name.formatted") or name.get("formatted") \
            or " ".join(str(x) for x in (given, family) if x)
        if isinstance(display, str) and display.strip():
            out["name"] = display.strip()[:300]
        return out

    async def _apply_profile(org_id: str, user: dict, profile: dict) -> dict:
        """The provider renamed someone, or gave them a new address. A new
        address must be at the workspace's domains, nobody else's, and the
        person in no other workspace (it is their sign-in everywhere)."""
        if profile.get("name") and profile["name"] != user.get("display_name"):
            await store.set_display_name(user["user_id"], profile["name"])
        new = profile.get("email")
        if new and new != user["email"]:
            if sso.domain_of(new) not in await _scim_domains(org_id):
                raise _ScimRefused(400, f"{new} is not at one of this workspace's domains",
                                   "invalidValue")
            if await store.get_user_by_email(new) is not None:
                raise _ScimRefused(409, f"{new} is another account's address", "uniqueness")
            others = [m for m in await store.memberships_for_user(user["user_id"])
                      if m["org_id"] != org_id]
            if others:
                raise _ScimRefused(400, "this person is in another workspace too — their "
                                        "address changes from their own account", "mutability")
            await store.set_user_email(user["user_id"], new)
            await _on_trail(org_id, "scim_renamed", new, who=_SCIM_WHO,
                            **{"from": user["email"], "to": new})
        return await store.get_user(user["user_id"]) or user

    async def _scim_body(request: Request) -> dict:
        try:
            body = await request.json()
        except ValueError as exc:
            raise _ScimRefused(400, "the body is not JSON", "invalidSyntax") from exc
        if not isinstance(body, dict):
            raise _ScimRefused(400, "the body is a JSON object", "invalidSyntax")
        return body

    def _active(value: object) -> bool:
        return value not in (False, "false", "False", "FALSE", 0)

    async def _member_user(org_id: str, user_id: str) -> dict:
        user = await store.get_user(user_id)
        if user is None or await store.role_in_org(user_id, org_id) is None:
            raise _ScimRefused(404, f"no user {user_id} in this workspace")
        return user

    @app.get("/v1/orgs/{org_id}/scim")
    async def scim_status(org_id: str, caller: Claims = Depends(_caller)) -> dict:
        _sso_admin(org_id, caller)
        held = await store.scim_of(org_id)
        return {"available": bool(public_url), "plan_ok": await _on_business(org_id),
                "base_url": scim_base, "enabled": held is not None, **(held or {})}

    @app.post("/v1/orgs/{org_id}/scim/token", status_code=201)
    async def scim_token(org_id: str, caller: Claims = Depends(_caller)) -> dict:
        """A new token for the identity provider — shown once; the old one stops."""
        _sso_admin(org_id, caller)
        if not public_url:
            raise HTTPException(409, "provisioning needs this service's public address "
                                     "(AXOR_IDENTITY_PUBLIC_URL)")
        if not await _on_business(org_id):
            raise HTTPException(402, {"code": "plan_required", "plan": "business",
                                      "detail": "provisioning (SCIM) is on the Business plan"})
        raw = "scim_" + secrets.token_urlsafe(32)
        await store.put_scim_token(org_id, digest(raw), caller.email or caller.user_id,
                                   _now_iso())
        await _on_trail(org_id, "scim_token_made", who=_who(caller))
        return {"token": raw, "base_url": scim_base}

    @app.delete("/v1/orgs/{org_id}/scim/token", status_code=204)
    async def scim_token_revoke(org_id: str, caller: Claims = Depends(_caller)) -> None:
        _sso_admin(org_id, caller)
        await store.delete_scim(org_id)
        await _on_trail(org_id, "scim_token_revoked", who=_who(caller))

    @app.get("/scim/v2/ServiceProviderConfig")
    async def scim_config() -> JSONResponse:
        return _scim({
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True}, "bulk": {"supported": False, "maxOperations": 0,
                                                   "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": 200},
            "changePassword": {"supported": False}, "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [{"type": "oauthbearertoken", "name": "Bearer token",
                                       "description": "the workspace's SCIM token"}]})

    @app.get("/scim/v2/ResourceTypes")
    async def scim_types() -> JSONResponse:
        return _scim({"schemas": [_LIST], "totalResults": 1, "Resources": [{
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "User",
            "name": "User", "endpoint": "/Users", "schema": _USER}]})

    @app.get("/scim/v2/Users")
    async def scim_users(filter: str = "", startIndex: int = 1, count: int = 100,  # noqa: A002, N803
                         org_id: str = Depends(_scim_org)) -> JSONResponse:
        members = await store.org_members(org_id)
        if filter:
            import re

            m = re.fullmatch(r'\s*(userName|emails(?:\.value)?|externalId)\s+eq\s+"([^"]*)"\s*',
                             filter, re.IGNORECASE)
            if not m:
                raise _ScimRefused(400, "only userName eq \"…\" is filtered on", "invalidFilter")
            want = m.group(2).strip().lower()
            members = [x for x in members if x["email"].lower() == want]
        start = max(1, startIndex)
        page = members[start - 1:start - 1 + max(0, min(count, 200))]
        users = [await store.get_user(x["user_id"]) for x in page]
        return _scim({"schemas": [_LIST], "totalResults": len(members), "startIndex": start,
                      "itemsPerPage": len(page),
                      "Resources": [_user_resource(u, True) for u in users if u]})

    @app.get("/scim/v2/Users/{user_id}")
    async def scim_user(user_id: str, org_id: str = Depends(_scim_org)) -> JSONResponse:
        return _scim(_user_resource(await _member_user(org_id, user_id), True))

    @app.post("/scim/v2/Users")
    async def scim_create(request: Request, org_id: str = Depends(_scim_org)) -> JSONResponse:
        body = await _scim_body(request)
        emails = [e.get("value") for e in body.get("emails") or [] if isinstance(e, dict)]
        email = _norm_email(str(body.get("userName") or next(iter(emails), "") or ""))
        if "@" not in email:
            raise _ScimRefused(400, "userName is the person's e-mail address", "invalidValue")
        if sso.domain_of(email) not in await _scim_domains(org_id):
            raise _ScimRefused(400, f"{email} is not at one of this workspace's domains",
                               "invalidValue")
        user = await store.get_user_by_email(email)
        if user is not None and await store.role_in_org(user["user_id"], org_id):
            raise _ScimRefused(409, f"{email} is in this workspace already", "uniqueness")
        if user is None:
            user_id = "usr_" + secrets.token_hex(8)
            await store.create_user(user_id, email, None, _now_iso(), email_verified=True)
            user = await store.get_user(user_id)
        name = _profile_of(body).get("name")
        if name and name != user.get("display_name"):
            await store.set_display_name(user["user_id"], name)
            user = await store.get_user(user["user_id"])
        active = _active(body.get("active", True))
        if active:
            await _provision(org_id, user["user_id"])
        return _scim(_user_resource(user, active), 201)

    @app.put("/scim/v2/Users/{user_id}")
    async def scim_replace(user_id: str, request: Request,
                           org_id: str = Depends(_scim_org)) -> JSONResponse:
        body = await _scim_body(request)
        user = await store.get_user(user_id)
        if user is None:
            raise _ScimRefused(404, f"no user {user_id}")
        user = await _apply_profile(org_id, user, _profile_of(body))
        active = _active(body.get("active", True))
        if active:
            await _provision(org_id, user_id)
        else:
            await _deprovision(org_id, user_id)
        return _scim(_user_resource(user, active))

    @app.patch("/scim/v2/Users/{user_id}")
    async def scim_patch(user_id: str, request: Request,
                         org_id: str = Depends(_scim_org)) -> JSONResponse:
        """Okta and Entra ID deactivate with a PATCH of ``active`` — as a path,
        or inside a value object; the op's case and the value's type vary."""
        body = await _scim_body(request)
        user = await store.get_user(user_id)
        if user is None:
            raise _ScimRefused(404, f"no user {user_id}")
        active = await store.role_in_org(user_id, org_id) is not None
        for op in body.get("Operations") or []:
            if not isinstance(op, dict) or str(op.get("op", "")).lower() not in ("replace", "add"):
                continue
            path, value = str(op.get("path") or ""), op.get("value")
            if path.lower() == "active":
                active = _active(value)
            elif not path and isinstance(value, dict) and "active" in value:
                active = _active(value["active"])
        user = await _apply_profile(org_id, user, _profile_of(body))
        if active:
            await _provision(org_id, user_id)
        else:
            await _deprovision(org_id, user_id)
        return _scim(_user_resource(user, active))

    @app.delete("/scim/v2/Users/{user_id}", status_code=204)
    async def scim_delete(user_id: str, org_id: str = Depends(_scim_org)) -> Response:
        if await store.get_user(user_id) is None:
            raise _ScimRefused(404, f"no user {user_id}")
        await _deprovision(org_id, user_id)
        return Response(status_code=204)

    # ── SCIM groups → roles ──────────────────────────────────────────────────
    # The provider pushes its groups and their people; an admin maps a group to
    # a role here. Once any group is mapped, a member's role follows their
    # groups: the highest mapped role among them, else the workspace's default.
    # The owner's role never moves; nobody becomes owner.
    _GROUP = "urn:ietf:params:scim:schemas:core:2.0:Group"
    _MAPPABLE = ("viewer", "member", "admin")

    def _group_resource(g: dict) -> dict:
        return {"schemas": [_GROUP], "id": g["group_id"], "displayName": g["display_name"],
                **({"externalId": g["external_id"]} if g.get("external_id") else {}),
                "members": [{"value": m} for m in g["members"]],
                "meta": {"resourceType": "Group", "created": g["created_ts"],
                         "location": f"{scim_base}/Groups/{g['group_id']}"}}

    async def _apply_group_roles(org_id: str, user_ids: set[str]) -> None:
        groups = await store.scim_groups_of(org_id)
        if not any(g["role"] for g in groups):
            return                       # no mapping: roles stay the workspace's
        held = await store.get_sso(org_id)
        default = held["default_role"] if held else "member"
        for uid in user_ids:
            current = await store.role_in_org(uid, org_id)
            if current is None or current == "owner":
                continue
            mapped = [g["role"] for g in groups if g["role"] and uid in g["members"]]
            role = max(mapped, key=_rank) if mapped else default
            if role != current:
                await store.set_member_role(uid, org_id, role)
                await _on_trail(org_id, "scim_role", await _email_of(uid), who=_SCIM_WHO,
                                **{"from": current, "to": role, "by_group": bool(mapped)})

    def _member_ids(raw: object) -> set[str]:
        return {str(m.get("value")) for m in raw or [] if isinstance(m, dict) and m.get("value")} \
            if isinstance(raw, list) else set()

    async def _group_or_404(org_id: str, group_id: str) -> dict:
        g = await store.scim_group(org_id, group_id)
        if g is None:
            raise _ScimRefused(404, f"no group {group_id} in this workspace")
        return g

    @app.get("/scim/v2/Groups")
    async def scim_groups(filter: str = "", startIndex: int = 1, count: int = 100,  # noqa: A002, N803
                          org_id: str = Depends(_scim_org)) -> JSONResponse:
        groups = await store.scim_groups_of(org_id)
        if filter:
            import re

            m = re.fullmatch(r'\s*displayName\s+eq\s+"([^"]*)"\s*', filter, re.IGNORECASE)
            if not m:
                raise _ScimRefused(400, 'only displayName eq "…" is filtered on', "invalidFilter")
            groups = [g for g in groups if g["display_name"].lower() == m.group(1).lower()]
        start = max(1, startIndex)
        page = groups[start - 1:start - 1 + max(0, min(count, 200))]
        return _scim({"schemas": [_LIST], "totalResults": len(groups), "startIndex": start,
                      "itemsPerPage": len(page),
                      "Resources": [_group_resource(g) for g in page]})

    @app.get("/scim/v2/Groups/{group_id}")
    async def scim_group(group_id: str, org_id: str = Depends(_scim_org)) -> JSONResponse:
        return _scim(_group_resource(await _group_or_404(org_id, group_id)))

    @app.post("/scim/v2/Groups")
    async def scim_group_create(request: Request, org_id: str = Depends(_scim_org)) -> JSONResponse:
        body = await _scim_body(request)
        name = str(body.get("displayName") or "").strip()[:300]
        if not name:
            raise _ScimRefused(400, "a group has a displayName", "invalidValue")
        held_groups = await store.scim_groups_of(org_id)
        if any(g["display_name"].lower() == name.lower() for g in held_groups):
            raise _ScimRefused(409, f"a group {name!r} exists", "uniqueness")
        g = {"group_id": "grp_" + secrets.token_hex(8), "org_id": org_id, "display_name": name,
             "external_id": str(body.get("externalId") or "")[:300] or None,
             "members": sorted(_member_ids(body.get("members"))), "role": None,
             "created_ts": _now_iso()}
        await store.put_scim_group(g)
        return _scim(_group_resource(g), 201)

    @app.put("/scim/v2/Groups/{group_id}")
    async def scim_group_replace(group_id: str, request: Request,
                                 org_id: str = Depends(_scim_org)) -> JSONResponse:
        body = await _scim_body(request)
        g = await _group_or_404(org_id, group_id)
        before = set(g["members"])
        g["display_name"] = str(body.get("displayName") or g["display_name"])[:300]
        g["members"] = sorted(_member_ids(body.get("members")))
        await store.put_scim_group(g)
        await _apply_group_roles(org_id, before | set(g["members"]))
        return _scim(_group_resource(g))

    @app.patch("/scim/v2/Groups/{group_id}")
    async def scim_group_patch(group_id: str, request: Request,
                               org_id: str = Depends(_scim_org)) -> JSONResponse:
        """Okta: add members, remove ``members[value eq "id"]``; Entra ID: Add /
        Remove with the members in the value; both: replace displayName."""
        import re

        body = await _scim_body(request)
        g = await _group_or_404(org_id, group_id)
        before = set(g["members"])
        members = set(g["members"])
        for op in body.get("Operations") or []:
            if not isinstance(op, dict):
                continue
            kind = str(op.get("op", "")).lower()
            path, value = str(op.get("path") or ""), op.get("value")
            one = re.fullmatch(r'\s*members\[\s*value\s+eq\s+"([^"]+)"\s*\]\s*', path)
            if kind == "add" and path.lower() == "members":
                members |= _member_ids(value)
            elif kind == "remove" and one:
                members.discard(one.group(1))
            elif kind == "remove" and path.lower() == "members":
                members = members - _member_ids(value) if value else set()
            elif kind == "replace" and path.lower() == "members":
                members = _member_ids(value)
            elif kind == "replace" and (path.lower() == "displayname" or isinstance(value, dict)):
                name = value.get("displayName") if isinstance(value, dict) else value
                if name:
                    g["display_name"] = str(name)[:300]
        g["members"] = sorted(members)
        await store.put_scim_group(g)
        await _apply_group_roles(org_id, before | members)
        return _scim(_group_resource(g))

    @app.delete("/scim/v2/Groups/{group_id}", status_code=204)
    async def scim_group_delete(group_id: str, org_id: str = Depends(_scim_org)) -> Response:
        g = await _group_or_404(org_id, group_id)
        await store.delete_scim_group(org_id, group_id)
        await _apply_group_roles(org_id, set(g["members"]))
        return Response(status_code=204)

    @app.get("/v1/orgs/{org_id}/scim/groups")
    async def scim_groups_admin(org_id: str, caller: Claims = Depends(_caller)) -> dict:
        _sso_admin(org_id, caller)
        return {"groups": [{"group_id": g["group_id"], "display_name": g["display_name"],
                            "members": len(g["members"]), "role": g["role"]}
                           for g in await store.scim_groups_of(org_id)]}

    @app.put("/v1/orgs/{org_id}/scim/groups/{group_id}")
    async def map_scim_group(org_id: str, group_id: str, body: RoleBody,
                             caller: Claims = Depends(_caller)) -> dict:
        """The role a group gives (viewer, member, admin), or "none"."""
        _sso_admin(org_id, caller)
        g = await store.scim_group(org_id, group_id)
        if g is None:
            raise HTTPException(404, "no such group")
        if body.role not in (*_MAPPABLE, "none"):
            raise HTTPException(422, f"role is one of {[*_MAPPABLE, 'none']}")
        before = g["role"]
        g["role"] = None if body.role == "none" else body.role
        await store.put_scim_group(g)
        if before != g["role"]:
            await _on_trail(org_id, "scim_group_role", g["display_name"], who=_who(caller),
                            group=g["display_name"], to=body.role)
        everyone = {m["user_id"] for m in await store.org_members(org_id)}
        await _apply_group_roles(org_id, everyone)
        return {"group_id": group_id, "role": g["role"]}

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
