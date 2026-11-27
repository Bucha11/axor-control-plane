"""System of record for identity: users, orgs, memberships, refresh tokens.

Postgres in production (asyncpg), SQLite (aiosqlite) for dev and tests — the
schema sticks to portable types, mirroring axor-backend's storage. This module
persists and looks up; it makes no policy decisions (hashing, token lifetimes,
and role checks live in their own modules).
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

metadata = MetaData()

orgs = Table(
    "orgs", metadata,
    Column("org_id", String(64), primary_key=True),
    Column("name", String(200), nullable=False),
    # the plan tier: community | team | security | enterprise. The token carries
    # it so downstream services gate features without a second lookup.
    Column("tier", String(32), nullable=False, default="community"),
    Column("created_ts", String(40), nullable=False),
    # the date the license that set `tier` runs to (licensing.py); past it the
    # workspace is on the free plan again
    Column("tier_expires", String(40), nullable=True),
    # sign in again after this many hours, whatever the refresh token says
    # (the workspace's session policy; NULL: the deployment's refresh TTL)
    Column("session_hours", Integer, nullable=True),
)

users = Table(
    "users", metadata,
    Column("user_id", String(64), primary_key=True),
    # email is the login handle; unique and stored lower-cased by the app layer.
    Column("email", String(320), nullable=False, unique=True),
    # argon2 encoded hash; NULL for an account made through GitHub / Google
    # until its owner sets a password
    Column("password_hash", String(200), nullable=True),
    Column("created_ts", String(40), nullable=False),
    # a provider vouched for the address (or, later, the verification link)
    Column("email_verified", Boolean, nullable=False, default=False),
    # the name the company's identity provider gives (SCIM); shown, never a key
    Column("display_name", String(300), nullable=True),
)

oauth_identities = Table(
    "oauth_identities", metadata,
    Column("provider", String(24), primary_key=True),
    Column("subject", String(128), primary_key=True),  # the provider's stable id
    Column("user_id", String(64), nullable=False, index=True),
    Column("email", String(320), nullable=False),
    Column("created_ts", String(40), nullable=False),
)

oauth_states = Table(
    "oauth_states", metadata,
    Column("state_hash", String(64), primary_key=True),
    Column("provider", String(24), nullable=False),
    Column("verifier", String(128), nullable=False),
    Column("return_to", String(1000), nullable=False),
    Column("expires_ts", String(40), nullable=False),
)

login_codes = Table(
    "login_codes", metadata,
    Column("code_hash", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False),
    Column("created", Boolean, nullable=False, default=False),  # a new account
    Column("expires_ts", String(40), nullable=False),
)

memberships = Table(
    "memberships", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", String(64), nullable=False, index=True),
    Column("org_id", String(64), nullable=False, index=True),
    Column("role", String(24), nullable=False),  # owner | admin | member | viewer
    Column("created_ts", String(40), nullable=False),
    # a user has at most one role per org (many-to-many identity↔org)
    UniqueConstraint("user_id", "org_id", name="uq_membership_user_org"),
)

refresh_tokens = Table(
    "refresh_tokens", metadata,
    # only the hash is stored; the raw token is shown once to the client
    Column("token_hash", String(64), primary_key=True),  # sha256 hex
    Column("user_id", String(64), nullable=False, index=True),
    Column("org_id", String(64), nullable=False),
    Column("expires_ts", String(40), nullable=False),
    Column("revoked", Boolean, nullable=False, default=False),
    Column("created_ts", String(40), nullable=False),
    # when the person actually signed in: carried through every rotation, so a
    # session policy counts from the sign-in, not from the last refresh
    Column("session_ts", String(40), nullable=True),
    # the session this token belongs to — every rotation of one sign-in shares
    # it, and the access tokens carry it as ``sid``. NULL on rows from before
    # it existed: those read their own token_hash as their family.
    Column("family", String(40), nullable=True, index=True),
)

# A person's membership epoch in one workspace: bumped when they are removed or
# their role changes. Access tokens carry the epoch they were issued at
# (``ep``); one below the current epoch speaks for a membership that is gone.
# Kept apart from ``memberships`` so a removal (the row deleted) keeps it.
token_epochs = Table(
    "token_epochs", metadata,
    Column("user_id", String(64), primary_key=True),
    Column("org_id", String(64), primary_key=True),
    Column("epoch", Integer, nullable=False),
)

# What was revoked recently, as the control plane reads it (/v1/revocations):
# ``session`` rows name a session id (logout, a reused refresh token, sessions
# ended by an admin); ``member`` rows name sha256("<user>:<org>") and the epoch
# it was bumped to. ``ts`` is epoch seconds. Only rows younger than an access
# token's lifetime matter: an older token has expired by itself.
revocations = Table(
    "revocations", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("kind", String(8), nullable=False),
    Column("key", String(64), nullable=False, index=True),
    Column("epoch", Integer, nullable=True),
    Column("reason", String(16), nullable=False),
    Column("ts", Integer, nullable=False, index=True),
)


def member_key(user_id: str, org_id: str) -> str:
    """How the revocation feed names a membership without naming the person."""
    return hashlib.sha256(f"{user_id}:{org_id}".encode()).hexdigest()

email_tokens = Table(
    "email_tokens", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False, index=True),
    Column("purpose", String(16), nullable=False),  # verify | reset
    Column("email", String(320), nullable=False),
    Column("expires_ts", String(40), nullable=False),
)


# An invitation to an org: a single-use link (only its hash is kept), for a
# role, optionally for one address, until it expires or is revoked.
invites = Table(
    "invites", metadata,
    Column("invite_id", String(24), primary_key=True),      # shown in lists
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("org_id", String(64), nullable=False, index=True),
    Column("role", String(24), nullable=False),
    Column("email", String(320), nullable=True),            # for one address, or anyone
    Column("created_by", String(64), nullable=False),
    Column("created_ts", String(40), nullable=False),
    Column("expires_ts", String(40), nullable=False),
    Column("used_by", String(64), nullable=True),
    Column("used_ts", String(40), nullable=True),
    Column("revoked", Boolean, nullable=False, default=False),
)

# A workspace's single sign-on (sso.py): its OpenID provider, the client this
# service is registered as there, and the e-mail domains it signs people in
# for. The secret is the provider's credential for this service: never shown
# again once saved.
org_sso = Table(
    "org_sso", metadata,
    Column("org_id", String(64), primary_key=True),
    Column("issuer", String(500), nullable=False),
    Column("client_id", String(300), nullable=False),
    Column("client_secret", String(500), nullable=False),
    Column("domains", Text, nullable=False),
    Column("enforce", Boolean, nullable=False, default=False),
    Column("default_role", String(24), nullable=False, default="member"),
    Column("updated_by", String(64), nullable=False),
    Column("updated_ts", String(40), nullable=False),
    # oidc | saml — and SAML's provider (saml.py): its SSO URL, entity id, cert
    Column("protocol", String(8), nullable=False, default="oidc"),
    Column("saml_sso_url", String(1000), nullable=False, default=""),
    Column("saml_entity_id", String(500), nullable=False, default=""),
    Column("saml_cert", Text, nullable=False, default=""),
    # sign our requests with the workspace's key (ADFS asks); take sign-ins
    # started on the provider's dashboard
    Column("saml_sign_requests", Boolean, nullable=False, default=False),
    Column("saml_idp_initiated", Boolean, nullable=False, default=False),
)

# The workspace's own SAML key pair: it signs our requests, providers encrypt
# assertions to it. The certificate is in our metadata; the key never leaves.
saml_sp_keys = Table(
    "saml_sp_keys", metadata,
    Column("org_id", String(64), primary_key=True),
    Column("key_pem", Text, nullable=False),
    Column("cert_pem", Text, nullable=False),
    Column("created_ts", String(40), nullable=False),
)

# The bearer token a workspace's identity provider provisions people with
# (SCIM, scim.py): only its hash, one per workspace.
scim_tokens = Table(
    "scim_tokens", metadata,
    Column("org_id", String(64), primary_key=True),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("created_by", String(320), nullable=False),
    Column("created_ts", String(40), nullable=False),
    Column("last_used_ts", String(40), nullable=True),
)

# A group the identity provider pushes (SCIM), its people, and the role an
# admin mapped to it: a member's role is the highest of their groups'.
scim_groups = Table(
    "scim_groups", metadata,
    Column("group_id", String(40), primary_key=True),
    Column("org_id", String(64), nullable=False, index=True),
    Column("display_name", String(300), nullable=False),
    Column("external_id", String(300), nullable=True),
    Column("members", Text, nullable=False, default="[]"),
    Column("role", String(24), nullable=True),
    Column("created_ts", String(40), nullable=False),
)


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url)


_BASELINE_REV = "0001"


def _run_migrations(sync_conn: Any) -> None:  # noqa: ANN401 - sync Connection
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.attributes["connection"] = sync_conn

    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    if "users" in tables and "alembic_version" not in tables:
        command.stamp(cfg, _BASELINE_REV)
    command.upgrade(cfg, "head")


async def init_db(engine: AsyncEngine) -> None:
    """Bring the schema to head via alembic at boot — same posture as the
    control-plane backend: fresh DBs get the baseline, legacy DBs are stamped
    then upgraded, future changes ship as new revisions."""
    async with engine.begin() as conn:
        await conn.run_sync(_run_migrations)


class Store:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine

    # ── orgs ──────────────────────────────────────────────────────────────────
    async def create_org(self, org_id: str, name: str, tier: str, ts: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(orgs).values(
                org_id=org_id, name=name, tier=tier, created_ts=ts))

    async def get_org(self, org_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(orgs).where(orgs.c.org_id == org_id))).first()
        if row is None:
            return None
        return {"org_id": row.org_id, "name": row.name, "tier": row.tier,
                "created_ts": row.created_ts, "tier_expires": row.tier_expires,
                "session_hours": row.session_hours}

    async def set_session_hours(self, org_id: str, hours: int | None) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(update(orgs).where(orgs.c.org_id == org_id)
                               .values(session_hours=hours))

    async def set_org_tier(self, org_id: str, tier: str, expires: str | None = None) -> bool:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(orgs).where(orgs.c.org_id == org_id).values(tier=tier,
                                                                   tier_expires=expires))
        return bool(result.rowcount)

    # ── users ─────────────────────────────────────────────────────────────────
    async def create_user(self, user_id: str, email: str, password_hash: str | None,
                          ts: str, *, email_verified: bool = False) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(users).values(
                user_id=user_id, email=email, password_hash=password_hash,
                created_ts=ts, email_verified=email_verified))

    async def create_account(self, user_id: str, email: str, password_hash: str | None,
                             org_id: str, org_name: str, ts: str, *,
                             email_verified: bool = False, new_user: bool = True) -> None:
        """A user, their workspace and their ownership of it — in ONE
        transaction (QA-230): a failure in any step leaves none of them, so an
        address is never left with an account and no workspace. ``new_user``
        False: the user exists (an account left without one) and only gets its
        workspace."""
        async with self.engine.begin() as conn:
            if new_user:
                await conn.execute(insert(users).values(
                    user_id=user_id, email=email, password_hash=password_hash,
                    created_ts=ts, email_verified=email_verified))
            await conn.execute(insert(orgs).values(
                org_id=org_id, name=org_name, tier="community", created_ts=ts))
            await conn.execute(insert(memberships).values(
                user_id=user_id, org_id=org_id, role="owner", created_ts=ts))

    async def count_users(self) -> int:
        """How many users exist at all — the whole table, not one org's.

        `AXOR_IDENTITY_SIGNUP=first-only` is answered with this: the first signup
        on a fresh install is the operator claiming it, and every later one is a
        stranger. Counting rather than reading a flag means the decision survives
        a restart, a redeploy and a restored dump, because it is derived from the
        data instead of stored beside it.
        """
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(func.count()).select_from(users))).first()
        return int(row[0]) if row else 0

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(users).where(users.c.email == email))).first()
        return self._user_row(row)

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(users).where(users.c.user_id == user_id))).first()
        return self._user_row(row)

    async def mark_verified(self, user_id: str, *, drop_password: bool = False) -> None:
        values: dict[str, Any] = {"email_verified": True}
        if drop_password:
            values["password_hash"] = None
        async with self.engine.begin() as conn:
            await conn.execute(update(users).where(users.c.user_id == user_id)
                               .values(**values))

    async def update_password(self, user_id: str, password_hash: str) -> bool:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(users).where(users.c.user_id == user_id)
                .values(password_hash=password_hash))
        return bool(result.rowcount)

    @staticmethod
    def _user_row(row: Any) -> dict[str, Any] | None:  # noqa: ANN401 - Row|None
        if row is None:
            return None
        return {"user_id": row.user_id, "email": row.email,
                "password_hash": row.password_hash, "created_ts": row.created_ts,
                "email_verified": bool(row.email_verified),
                "display_name": row.display_name}

    async def set_user_email(self, user_id: str, email: str) -> None:
        """A new address the company's provider vouches for (SCIM)."""
        async with self.engine.begin() as conn:
            await conn.execute(update(users).where(users.c.user_id == user_id)
                               .values(email=email, email_verified=True))

    async def set_display_name(self, user_id: str, name: str | None) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(update(users).where(users.c.user_id == user_id)
                               .values(display_name=name))

    # ── memberships ───────────────────────────────────────────────────────────
    async def add_membership(self, user_id: str, org_id: str, role: str,
                            ts: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(memberships).values(
                user_id=user_id, org_id=org_id, role=role, created_ts=ts))

    async def role_in_org(self, user_id: str, org_id: str) -> str | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(memberships.c.role).where(
                    memberships.c.user_id == user_id,
                    memberships.c.org_id == org_id))).first()
        return row.role if row else None

    async def memberships_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Every (org, role, tier) the user belongs to — joined so the login
        response can show which orgs are available and their tiers."""
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(memberships.c.org_id, memberships.c.role,
                       orgs.c.name, orgs.c.tier)
                .select_from(memberships.join(orgs, memberships.c.org_id == orgs.c.org_id))
                .where(memberships.c.user_id == user_id)
                .order_by(memberships.c.created_ts))).all()
        return [{"org_id": r.org_id, "role": r.role, "name": r.name, "tier": r.tier}
                for r in rows]

    async def org_members(self, org_id: str) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(
                select(memberships.c.user_id, memberships.c.role, memberships.c.created_ts,
                       users.c.email)
                .select_from(memberships.join(users, memberships.c.user_id == users.c.user_id))
                .where(memberships.c.org_id == org_id)
                .order_by(memberships.c.created_ts))).all()
        return [{"user_id": r.user_id, "email": r.email, "role": r.role, "since": r.created_ts}
                for r in rows]

    async def set_member_role(self, user_id: str, org_id: str, role: str) -> bool:
        """A new role; a CHANGED one also bumps the membership epoch, so access
        tokens carrying the old role stop working at once (QA-154)."""
        async with self.engine.begin() as conn:
            before = (await conn.execute(select(memberships.c.role).where(
                memberships.c.user_id == user_id, memberships.c.org_id == org_id))).first()
            result = await conn.execute(update(memberships).where(
                memberships.c.user_id == user_id, memberships.c.org_id == org_id,
            ).values(role=role))
            if before is not None and before.role != role:
                await self._bump_epoch(conn, user_id, org_id, "role")
        return bool(result.rowcount)

    async def remove_member(self, user_id: str, org_id: str) -> bool:
        """The membership ends, and with it every access token for it (the
        epoch is bumped in the same transaction — QA-154)."""
        async with self.engine.begin() as conn:
            result = await conn.execute(delete(memberships).where(
                memberships.c.user_id == user_id, memberships.c.org_id == org_id))
            if result.rowcount:
                await self._bump_epoch(conn, user_id, org_id, "removed")
        return bool(result.rowcount)

    @staticmethod
    async def _bump_epoch(conn: Any, user_id: str, org_id: str,  # noqa: ANN401
                          reason: str) -> None:
        row = (await conn.execute(select(token_epochs.c.epoch).where(
            token_epochs.c.user_id == user_id, token_epochs.c.org_id == org_id))).first()
        epoch = (row.epoch if row else 0) + 1
        if row is None:
            await conn.execute(insert(token_epochs).values(
                user_id=user_id, org_id=org_id, epoch=epoch))
        else:
            await conn.execute(update(token_epochs).where(
                token_epochs.c.user_id == user_id, token_epochs.c.org_id == org_id,
            ).values(epoch=epoch))
        await conn.execute(insert(revocations).values(
            kind="member", key=member_key(user_id, org_id), epoch=epoch, reason=reason,
            ts=int(time.time())))

    async def role_and_epoch(self, user_id: str, org_id: str) -> tuple[str | None, int]:
        """The role held now and the membership epoch, read together — so a
        token is never minted with one role and the epoch of another."""
        async with self.engine.connect() as conn:
            role = (await conn.execute(select(memberships.c.role).where(
                memberships.c.user_id == user_id, memberships.c.org_id == org_id))).first()
            epoch = (await conn.execute(select(token_epochs.c.epoch).where(
                token_epochs.c.user_id == user_id, token_epochs.c.org_id == org_id))).first()
        return (role.role if role else None), (epoch.epoch if epoch else 0)

    # ── invites ───────────────────────────────────────────────────────────────
    async def create_invite(self, invite_id: str, token_hash: str, org_id: str, role: str,
                            email: str | None, created_by: str, ts: str,
                            expires: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(invites).values(
                invite_id=invite_id, token_hash=token_hash, org_id=org_id, role=role,
                email=email, created_by=created_by, created_ts=ts, expires_ts=expires,
                revoked=False))

    async def invite_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(invites).where(
                invites.c.token_hash == token_hash))).first()
        return dict(row._mapping) if row else None

    async def open_invites(self, org_id: str, now: str) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(invites).where(
                invites.c.org_id == org_id, invites.c.used_by.is_(None),
                invites.c.revoked.is_(False), invites.c.expires_ts > now,
            ).order_by(invites.c.created_ts.desc()))).all()
        return [{k: v for k, v in dict(r._mapping).items() if k != "token_hash"} for r in rows]

    async def use_invite(self, token_hash: str, user_id: str, ts: str) -> bool:
        """Single use: True only for the request that took it."""
        async with self.engine.begin() as conn:
            res = await conn.execute(update(invites).where(
                invites.c.token_hash == token_hash, invites.c.used_by.is_(None),
                invites.c.revoked.is_(False)).values(used_by=user_id, used_ts=ts))
        return (res.rowcount or 0) == 1

    async def revoke_invite(self, org_id: str, invite_id: str) -> bool:
        async with self.engine.begin() as conn:
            res = await conn.execute(update(invites).where(
                invites.c.org_id == org_id, invites.c.invite_id == invite_id,
                invites.c.used_by.is_(None)).values(revoked=True))
        return (res.rowcount or 0) == 1

    # ── refresh tokens ────────────────────────────────────────────────────────
    async def store_refresh(self, token_hash: str, user_id: str, org_id: str,
                           expires_ts: str, ts: str, session_ts: str | None = None,
                           family: str | None = None) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(refresh_tokens).values(
                token_hash=token_hash, user_id=user_id, org_id=org_id,
                expires_ts=expires_ts, revoked=False, created_ts=ts,
                session_ts=session_ts or ts, family=family))

    async def get_refresh(self, token_hash: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(refresh_tokens).where(
                    refresh_tokens.c.token_hash == token_hash))).first()
        if row is None:
            return None
        return {"token_hash": row.token_hash, "user_id": row.user_id,
                "org_id": row.org_id, "expires_ts": row.expires_ts,
                "revoked": bool(row.revoked), "created_ts": row.created_ts,
                "session_ts": row.session_ts or row.created_ts,
                "family": row.family or row.token_hash}

    async def end_session(self, family: str, reason: str) -> None:
        """One session ends: every refresh token of its family is revoked, and
        its id goes on the revocation feed so its access tokens stop too
        (logout, a reused refresh token — QA-240)."""
        async with self.engine.begin() as conn:
            await conn.execute(update(refresh_tokens).where(
                (refresh_tokens.c.family == family) | (refresh_tokens.c.token_hash == family),
            ).values(revoked=True))
            await conn.execute(insert(revocations).values(
                kind="session", key=family, reason=reason, ts=int(time.time())))

    async def session_revoked(self, family: str) -> bool:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(revocations.c.id).where(
                revocations.c.kind == "session", revocations.c.key == family))).first()
        return row is not None

    async def recent_revocations(self, since: int) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(revocations).where(
                revocations.c.ts >= since).order_by(revocations.c.id))).all()
        return [{"kind": r.kind, "key": r.key, "epoch": r.epoch, "reason": r.reason}
                for r in rows]

    async def revoke_all_refresh(self, user_id: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(update(refresh_tokens)
                               .where(refresh_tokens.c.user_id == user_id)
                               .values(revoked=True))

    async def end_sessions_in_org(self, org_id: str, *, keep_user: str | None = None) -> int:
        """Every live sign-in to one workspace ends (but ``keep_user``'s)."""
        where = [refresh_tokens.c.org_id == org_id, refresh_tokens.c.revoked.is_(False)]
        if keep_user:
            where.append(refresh_tokens.c.user_id != keep_user)
        async with self.engine.begin() as conn:
            users_out = (await conn.execute(
                select(refresh_tokens.c.user_id).where(*where).distinct())).all()
            await conn.execute(update(refresh_tokens).where(*where).values(revoked=True))
        return len(users_out)

    async def revoke_refresh_in_org(self, user_id: str, org_id: str) -> None:
        """Their sessions in one workspace end; those in others go on."""
        async with self.engine.begin() as conn:
            await conn.execute(update(refresh_tokens)
                               .where(refresh_tokens.c.user_id == user_id,
                                      refresh_tokens.c.org_id == org_id)
                               .values(revoked=True))

    # ── SCIM ──────────────────────────────────────────────────────────────────
    async def put_scim_token(self, org_id: str, token_hash: str, by: str, ts: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(delete(scim_tokens).where(scim_tokens.c.org_id == org_id))
            await conn.execute(insert(scim_tokens).values(
                org_id=org_id, token_hash=token_hash, created_by=by, created_ts=ts))

    async def scim_of(self, org_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(scim_tokens)
                                      .where(scim_tokens.c.org_id == org_id))).first()
        return None if row is None else {"org_id": row.org_id, "created_by": row.created_by,
                                         "created_ts": row.created_ts,
                                         "last_used_ts": row.last_used_ts}

    async def org_for_scim(self, token_hash: str, ts: str) -> str | None:
        async with self.engine.begin() as conn:
            row = (await conn.execute(select(scim_tokens.c.org_id)
                                      .where(scim_tokens.c.token_hash == token_hash))).first()
            if row is not None:
                await conn.execute(update(scim_tokens).where(scim_tokens.c.org_id == row.org_id)
                                   .values(last_used_ts=ts))
        return row.org_id if row else None

    async def delete_scim(self, org_id: str) -> bool:
        async with self.engine.begin() as conn:
            done = await conn.execute(delete(scim_tokens).where(scim_tokens.c.org_id == org_id))
        return bool(done.rowcount)

    @staticmethod
    def _group_row(r: Any) -> dict[str, Any]:  # noqa: ANN401 - a Row
        import json

        return {"group_id": r.group_id, "org_id": r.org_id, "display_name": r.display_name,
                "external_id": r.external_id, "members": json.loads(r.members),
                "role": r.role, "created_ts": r.created_ts}

    async def scim_groups_of(self, org_id: str) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(scim_groups).where(scim_groups.c.org_id == org_id)
                                       .order_by(scim_groups.c.display_name))).all()
        return [self._group_row(r) for r in rows]

    async def scim_group(self, org_id: str, group_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(scim_groups).where(
                scim_groups.c.org_id == org_id, scim_groups.c.group_id == group_id))).first()
        return self._group_row(row) if row else None

    async def put_scim_group(self, group: dict[str, Any]) -> None:
        import json

        values = {**group, "members": json.dumps(sorted(set(group["members"])))}
        async with self.engine.begin() as conn:
            done = await conn.execute(update(scim_groups).where(
                scim_groups.c.group_id == group["group_id"]).values(**values))
            if not done.rowcount:
                await conn.execute(insert(scim_groups).values(**values))

    async def delete_scim_group(self, org_id: str, group_id: str) -> bool:
        async with self.engine.begin() as conn:
            done = await conn.execute(delete(scim_groups).where(
                scim_groups.c.org_id == org_id, scim_groups.c.group_id == group_id))
        return bool(done.rowcount)

    # ── sign in with a provider ───────────────────────────────────────────────
    async def user_for_identity(self, provider: str, subject: str) -> str | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(oauth_identities.c.user_id).where(
                oauth_identities.c.provider == provider,
                oauth_identities.c.subject == subject))).first()
        return row.user_id if row else None

    async def link_identity(self, provider: str, subject: str, user_id: str,
                            email: str, ts: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(oauth_identities).values(
                provider=provider, subject=subject, user_id=user_id, email=email,
                created_ts=ts))

    async def identities_for_user(self, user_id: str) -> list[str]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(oauth_identities.c.provider).where(
                oauth_identities.c.user_id == user_id))).all()
        return sorted({r.provider for r in rows})

    async def put_state(self, state_hash: str, provider: str, verifier: str,
                        return_to: str, expires_ts: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(oauth_states).values(
                state_hash=state_hash, provider=provider, verifier=verifier,
                return_to=return_to, expires_ts=expires_ts))

    async def take_state(self, state_hash: str) -> dict[str, Any] | None:
        """Single use: the row is gone once read, so a replayed callback finds
        nothing."""
        async with self.engine.begin() as conn:
            row = (await conn.execute(select(oauth_states).where(
                oauth_states.c.state_hash == state_hash))).first()
            if row is None:
                return None
            gone = await conn.execute(delete(oauth_states).where(
                oauth_states.c.state_hash == state_hash))
        if not gone.rowcount:
            return None
        return {"provider": row.provider, "verifier": row.verifier,
                "return_to": row.return_to, "expires_ts": row.expires_ts}

    async def put_login_code(self, code_hash: str, user_id: str, created: bool,
                             expires_ts: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(login_codes).values(
                code_hash=code_hash, user_id=user_id, created=created,
                expires_ts=expires_ts))

    async def take_login_code(self, code_hash: str) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            row = (await conn.execute(select(login_codes).where(
                login_codes.c.code_hash == code_hash))).first()
            if row is None:
                return None
            gone = await conn.execute(delete(login_codes).where(
                login_codes.c.code_hash == code_hash))
        if not gone.rowcount:
            return None
        return {"user_id": row.user_id, "created": bool(row.created),
                "expires_ts": row.expires_ts}

    # ── e-mail links ──────────────────────────────────────────────────────────
    async def put_email_token(self, token_hash: str, user_id: str, purpose: str,
                              email: str, expires_ts: str) -> None:
        async with self.engine.begin() as conn:
            # one live link per purpose: a new one retires the old
            await conn.execute(delete(email_tokens).where(
                email_tokens.c.user_id == user_id, email_tokens.c.purpose == purpose))
            await conn.execute(insert(email_tokens).values(
                token_hash=token_hash, user_id=user_id, purpose=purpose, email=email,
                expires_ts=expires_ts))

    async def take_email_token(self, token_hash: str, purpose: str) -> dict[str, Any] | None:
        async with self.engine.begin() as conn:
            row = (await conn.execute(select(email_tokens).where(
                email_tokens.c.token_hash == token_hash,
                email_tokens.c.purpose == purpose))).first()
            if row is None:
                return None
            gone = await conn.execute(delete(email_tokens).where(
                email_tokens.c.token_hash == token_hash))
        if not gone.rowcount:
            return None
        return {"user_id": row.user_id, "email": row.email, "expires_ts": row.expires_ts}

    async def sweep_expired(self, now_ts: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(delete(email_tokens).where(email_tokens.c.expires_ts <= now_ts))
            await conn.execute(delete(oauth_states).where(oauth_states.c.expires_ts <= now_ts))
            await conn.execute(delete(login_codes).where(login_codes.c.expires_ts <= now_ts))

    # ── single sign-on ────────────────────────────────────────────────────────
    @staticmethod
    def _sso_row(r: Any) -> dict[str, Any]:  # noqa: ANN401 - a Row
        import json

        return {"org_id": r.org_id, "issuer": r.issuer, "client_id": r.client_id,
                "client_secret": r.client_secret, "domains": json.loads(r.domains),
                "enforce": bool(r.enforce), "default_role": r.default_role,
                "updated_by": r.updated_by, "updated_ts": r.updated_ts,
                "protocol": r.protocol or "oidc", "saml_sso_url": r.saml_sso_url or "",
                "saml_entity_id": r.saml_entity_id or "", "saml_cert": r.saml_cert or "",
                "saml_sign_requests": bool(r.saml_sign_requests),
                "saml_idp_initiated": bool(r.saml_idp_initiated)}

    async def sp_key(self, org_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(saml_sp_keys).where(
                saml_sp_keys.c.org_id == org_id))).first()
        return {"key_pem": row.key_pem, "cert_pem": row.cert_pem} if row else None

    async def put_sp_key(self, org_id: str, key_pem: str, cert_pem: str, ts: str) -> None:
        """First one wins: two requests racing to make a key keep one."""
        from sqlalchemy.exc import IntegrityError

        try:
            async with self.engine.begin() as conn:
                await conn.execute(insert(saml_sp_keys).values(
                    org_id=org_id, key_pem=key_pem, cert_pem=cert_pem, created_ts=ts))
        except IntegrityError:
            pass

    async def first_use(self, key: str, expires_ts: str) -> bool:
        """True the first time ``key`` is seen before it expires — an
        assertion ID, say. A second insert of the same key fails."""
        from sqlalchemy.exc import IntegrityError

        try:
            async with self.engine.begin() as conn:
                await conn.execute(insert(oauth_states).values(
                    state_hash=key, provider="seen", verifier="", return_to="",
                    expires_ts=expires_ts))
        except IntegrityError:
            return False
        return True

    async def get_sso(self, org_id: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(select(org_sso).where(org_sso.c.org_id == org_id))).first()
        return self._sso_row(row) if row else None

    async def all_sso(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            rows = (await conn.execute(select(org_sso))).all()
        return [self._sso_row(r) for r in rows]

    async def sso_for_domain(self, domain: str) -> dict[str, Any] | None:
        """The workspace that signs this domain in, if one does (a domain is
        one workspace's at most — the app keeps it so)."""
        return next((c for c in await self.all_sso() if domain in c["domains"]), None)

    async def put_sso(self, org_id: str, values: dict[str, Any]) -> None:
        import json

        row = {**values, "domains": json.dumps(sorted(values["domains"]))}
        async with self.engine.begin() as conn:
            done = await conn.execute(update(org_sso).where(org_sso.c.org_id == org_id)
                                      .values(**row))
            if not done.rowcount:
                await conn.execute(insert(org_sso).values(org_id=org_id, **row))

    async def delete_sso(self, org_id: str) -> bool:
        async with self.engine.begin() as conn:
            done = await conn.execute(delete(org_sso).where(org_sso.c.org_id == org_id))
        return bool(done.rowcount)

    async def revoke_refresh(self, token_hash: str) -> bool:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(refresh_tokens)
                .where(refresh_tokens.c.token_hash == token_hash)
                .values(revoked=True))
        return bool(result.rowcount)
