"""Guest sessions — a hosted visitor works before signing up.

A guest is a bearer token (``gs_…``) mapped to a throwaway org ``guest_…``:
the visitor reads and writes that org like a tenant (scopes read, ingest,
operate — never admin), for ``GUEST_TTL_S``. Signing up claims the org's rows
into the account (``Store.claim_guest``); a guest org nobody claimed is swept
``GUEST_KEEP_S`` after it expired.

Off unless ``AXOR_GUEST_SESSIONS`` is set: a self-hosted plane has no
anonymous visitors to welcome, and an open deployment (auth off) needs none.

What a guest may NOT do is what only makes sense with an account: share
links (they outlive the session), notifications, the regression corpus,
Lab deploys, the vault, keys and licences. Those answer 403
``account_required`` with the feature named, so the UI can ask for a sign-up
at the moment it is wanted.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from collections import deque
from datetime import UTC, datetime, timedelta

GUEST_TTL_S = 30 * 60
GUEST_KEEP_S = 24 * 3600        # unclaimed guest data is kept this long after expiry
GUEST_CAP = 500                 # live guests at once
PER_IP_PER_MIN = 6
TOKEN_PREFIX = "gs_"
ORG_PREFIX = "guest_"
GUEST_SCOPES = frozenset({"read", "ingest", "operate"})

# (method or "*", path prefix, path suffix, feature) — what needs an account
ACCOUNT_ONLY: tuple[tuple[str, str, str, str], ...] = (
    ("POST", "/v1/runs/", "/share", "share links"),
    ("*", "/v1/notifications", "", "notifications"),
    ("POST", "/v1/pins/", "", "the regression corpus"),
    ("*", "/v1/regression", "", "the regression corpus"),
    ("*", "/v1/lab/", "", "Lab deploys"),
    ("*", "/v1/vault", "", "the vault"),
    ("*", "/v1/keys", "", "API and node keys"),
    ("*", "/v1/license", "", "licences"),
)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def account_only(method: str, path: str) -> str | None:
    for m, prefix, suffix, feature in ACCOUNT_ONLY:
        if (m == "*" or m == method) and path.startswith(prefix) and path.endswith(suffix):
            return feature
    return None


def _iso(t: datetime) -> str:
    return t.isoformat()


class GuestLimit(Exception):
    """Too many guests right now, or too many from one address."""


class GuestIssuer:
    """Issues guest tokens: a per-address rate limit and a global cap, and an
    occasional sweep of unclaimed guest data. State that must survive a
    restart (the sessions) lives in the store; only the rate window is here."""

    def __init__(self, store, ttl_s: int = GUEST_TTL_S) -> None:  # noqa: ANN001
        self._store = store
        self._ttl = ttl_s
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = 0.0

    async def issue(self, address: str) -> dict:
        now_m = time.monotonic()
        hits = self._hits.setdefault(address, deque())
        while hits and now_m - hits[0] > 60:
            hits.popleft()
        if len(hits) >= PER_IP_PER_MIN:
            raise GuestLimit("too many guest sessions from this address — try again in a minute")
        now = datetime.now(UTC)
        if now_m - self._last_sweep > 60:
            self._last_sweep = now_m
            await self._store.sweep_guests(_iso(now - timedelta(seconds=GUEST_KEEP_S)))
            if len(self._hits) > 10_000:   # bound the rate table itself
                self._hits = {address: hits}
        if await self._store.live_guest_count(_iso(now)) >= GUEST_CAP:
            raise GuestLimit("guest sessions are full right now — sign in, or try again shortly")
        hits.append(now_m)
        token = TOKEN_PREFIX + secrets.token_urlsafe(24)
        org = ORG_PREFIX + secrets.token_hex(8)
        expires = now + timedelta(seconds=self._ttl)
        await self._store.create_guest(hash_token(token), org, _iso(now), _iso(expires))
        return {"token": token, "org": org, "expires_at": _iso(expires), "ttl_seconds": self._ttl}

    async def resolve(self, token: str) -> str | None:
        """The guest's org, while the session is live and unclaimed."""
        row = await self._store.guest_session(hash_token(token))
        if row is None or row["claimed_by"] is not None:
            return None
        if row["expires_ts"] <= _iso(datetime.now(UTC)):
            return None
        return row["org_id"]
