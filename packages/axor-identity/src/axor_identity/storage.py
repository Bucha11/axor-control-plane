"""System of record for identity: users, orgs, memberships, refresh tokens.

Postgres in production (asyncpg), SQLite (aiosqlite) for dev and tests — the
schema sticks to portable types, mirroring axor-backend's storage. This module
persists and looks up; it makes no policy decisions (hashing, token lifetimes,
and role checks live in their own modules).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
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
)

email_tokens = Table(
    "email_tokens", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(64), nullable=False, index=True),
    Column("purpose", String(16), nullable=False),  # verify | reset
    Column("email", String(320), nullable=False),
    Column("expires_ts", String(40), nullable=False),
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
                "created_ts": row.created_ts}

    async def set_org_tier(self, org_id: str, tier: str) -> bool:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(orgs).where(orgs.c.org_id == org_id).values(tier=tier))
        return bool(result.rowcount)

    # ── users ─────────────────────────────────────────────────────────────────
    async def create_user(self, user_id: str, email: str, password_hash: str | None,
                          ts: str, *, email_verified: bool = False) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(users).values(
                user_id=user_id, email=email, password_hash=password_hash,
                created_ts=ts, email_verified=email_verified))

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
                "email_verified": bool(row.email_verified)}

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

    # ── refresh tokens ────────────────────────────────────────────────────────
    async def store_refresh(self, token_hash: str, user_id: str, org_id: str,
                           expires_ts: str, ts: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(insert(refresh_tokens).values(
                token_hash=token_hash, user_id=user_id, org_id=org_id,
                expires_ts=expires_ts, revoked=False, created_ts=ts))

    async def get_refresh(self, token_hash: str) -> dict[str, Any] | None:
        async with self.engine.connect() as conn:
            row = (await conn.execute(
                select(refresh_tokens).where(
                    refresh_tokens.c.token_hash == token_hash))).first()
        if row is None:
            return None
        return {"token_hash": row.token_hash, "user_id": row.user_id,
                "org_id": row.org_id, "expires_ts": row.expires_ts,
                "revoked": bool(row.revoked), "created_ts": row.created_ts}

    async def revoke_all_refresh(self, user_id: str) -> None:
        async with self.engine.begin() as conn:
            await conn.execute(update(refresh_tokens)
                               .where(refresh_tokens.c.user_id == user_id)
                               .values(revoked=True))

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

    async def revoke_refresh(self, token_hash: str) -> bool:
        async with self.engine.begin() as conn:
            result = await conn.execute(
                update(refresh_tokens)
                .where(refresh_tokens.c.token_hash == token_hash)
                .values(revoked=True))
        return bool(result.rowcount)
