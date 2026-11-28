"""Verify axor-identity access tokens — the vendored client.

This is a copy of `axor_identity.verify` kept in axor-backend so the backend
has no package dependency on the identity service: the two share a token
FORMAT, not a library. When AXOR_IDENTITY_JWKS(_URL) is configured, a request
may authenticate with an identity access token; it is verified here, locally,
against the published public key — no callback to identity.

pyjwt + cryptography are imported lazily (the `axor-backend[identity]` extra),
so importing this module costs nothing when identity login is not configured.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("axor.backend.identity")

ISSUER = "axor-identity"
ALGORITHM = "EdDSA"


class IdentityError(Exception):
    """An access token was missing, malformed, expired, or not trusted."""


class UnknownKeyId(IdentityError):
    """The token names a `kid` this JWKS does not carry.

    Its own type because it is the ONE verification failure a refetch can fix:
    identity rotated its signing key and this process is holding the document
    from before. Every other failure — a bad signature, an expired token, a
    wrong issuer — refetching cannot help, and retrying on those would turn any
    unauthenticated caller into an outbound-request generator.
    """


@dataclass(frozen=True)
class Claims:
    user_id: str
    email: str
    org: str
    role: str
    tier: str
    # the identity service has seen proof the address is theirs (a provider
    # vouched, or a mailed link was followed). Absent in older tokens: False.
    email_verified: bool = False
    # the sign-in (refresh-token family) and the membership epoch it was issued
    # at — what the revocation feed is checked against. Absent in older tokens.
    session_id: str | None = None
    epoch: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Claims:
        try:
            return cls(user_id=payload["sub"], email=payload["eml"],
                       org=payload["org"], role=payload["role"],
                       tier=payload["tier"],
                       email_verified=payload.get("evf") is True,
                       session_id=payload.get("sid") if isinstance(payload.get("sid"), str)
                       else None,
                       epoch=payload.get("ep") if isinstance(payload.get("ep"), int) else 0)
        except KeyError as exc:
            raise IdentityError(f"token missing claim {exc}") from exc


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _public_key_from_jwk(jwk: dict[str, str]) -> Any:  # noqa: ANN401 - Ed25519PublicKey
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise IdentityError("unsupported JWK: expected an Ed25519 OKP key")
    try:
        return Ed25519PublicKey.from_public_bytes(_b64url_decode(jwk["x"]))
    except (KeyError, ValueError, TypeError) as exc:
        # A JWK whose `x` is truncated, unpadded or missing raised a raw
        # ValueError out of cryptography, past the IdentityError the caller
        # catches — so a malformed key in the document answered every login
        # with a 500 instead of a 401 naming the problem.
        raise IdentityError(f"malformed JWK for kid {jwk.get('kid')!r}: {exc}") from exc


def verify_access_token(token: str, jwks: dict[str, Any], *, issuer: str = ISSUER,
                        leeway: int = 30) -> Claims:
    """Validate `token` against `jwks` and return its Claims, or raise
    IdentityError. `jwks` is the parsed `/.well-known/jwks.json` document."""
    import jwt  # lazy: only a hosted, identity-configured server needs it

    keys = {k.get("kid"): k for k in jwks.get("keys", [])}
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise IdentityError(f"malformed token: {exc}") from exc
    jwk = keys.get(kid) or (jwks["keys"][0] if len(jwks.get("keys", [])) == 1 else None)
    if jwk is None:
        raise UnknownKeyId(f"no verifying key for kid {kid!r}")
    try:
        payload = jwt.decode(
            token, _public_key_from_jwk(jwk), algorithms=[ALGORITHM],
            issuer=issuer, leeway=leeway,
            options={"require": ["exp", "iss", "sub"]})
    except jwt.PyJWTError as exc:
        raise IdentityError(str(exc)) from exc
    return Claims.from_payload(payload)


def fetch_jwks(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Fetch a JWKS document over HTTP (std-lib only)."""
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read())


class JwksRefresher:
    """The verifying keys this process holds, and the one reason to refetch.

    "A server fetches once at boot and caches; the kid tells it when a refetch
    is due" was written here and never built: there was exactly one call to
    `fetch_jwks`, inside `AppConfig.from_env`. So the day identity rotated its
    signing key, every human login answered a flat 401 until somebody restarted
    the backend — and the next reader of this file believed a mechanism existed.

    The trigger is `UnknownKeyId` and nothing else, and it is rate-limited:
    presenting a token is unauthenticated, so an unbounded refetch would let
    anyone drive outbound requests from the backend. One fetch at a time (the
    lock), at most one per `min_interval`, and a fetch that fails leaves the
    old document in place rather than logging the process out of identity.
    """

    def __init__(
        self,
        document: dict[str, Any],
        url: str | None = None,
        *,
        min_interval: float = 60.0,
        fetch: Any = None,  # noqa: ANN401 - injected for tests
        clock: Any = None,  # noqa: ANN401
    ) -> None:
        self._document = document
        self._url = url
        self._min_interval = min_interval
        self._fetch = fetch or fetch_jwks
        self._clock = clock or time.monotonic
        self._last = float("-inf")
        self._lock = asyncio.Lock()

    @property
    def document(self) -> dict[str, Any]:
        return self._document

    async def refreshed(self) -> dict[str, Any] | None:
        """Refetch if there is a URL and enough time has passed; the new
        document, or None when there is nothing new to verify against."""
        if self._url is None:
            return None
        async with self._lock:
            now = self._clock()
            if now - self._last < self._min_interval:
                return None
            self._last = now
            try:
                document = await asyncio.to_thread(self._fetch, self._url)
            except Exception as exc:  # noqa: BLE001 - identity being down is not our 500
                log.warning("JWKS refetch from %s failed: %s", self._url, exc)
                return None
        self._document = document
        log.info("refetched the identity JWKS (a token named an unknown kid)")
        return document


def member_key(user_id: str, org_id: str) -> str:
    """How identity's revocation feed names a membership (sha256 of
    "<user>:<org>"), so the feed itself names nobody."""
    import hashlib

    return hashlib.sha256(f"{user_id}:{org_id}".encode()).hexdigest()


async def _fetch_feed(url: str) -> dict[str, Any]:
    import httpx  # noqa: PLC0415 - only a deployment with identity needs it

    async with httpx.AsyncClient(timeout=3.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


class RevocationFeed:
    """What identity revoked since an access token was issued (QA-154, QA-240).

    An access token is verified offline, so by itself it stays good until it
    expires — 15 minutes in which a removed teammate kept reading the workspace
    and a logged-out tab stayed signed in. Identity publishes, at
    ``/v1/revocations``, the sessions that ended (logout, a reused refresh
    token, an admin ending sign-ins) and the memberships whose epoch it bumped
    (a removal, a role change) over the last access-token lifetime. A token is
    refused when its ``sid`` is listed, or its ``ep`` is below the epoch listed
    for its person and workspace.

    The tradeoff, chosen over asking identity on every request: the feed is
    fetched at most every ``max_age`` seconds (lazily, when a person's token is
    presented — one fetch at a time) and cached, so a revocation takes effect
    within that interval rather than instantly, and identity stays off the
    path of every request. If identity cannot be reached the last copy is kept
    and tokens are accepted on it — fail-open, logged — because the worst case
    is the token's own expiry, which was the behaviour before; refusing every
    signed-in person whenever identity blips would be worse.
    """

    def __init__(self, url: str, *, max_age: float = 5.0,
                 fetch: Any = None,  # noqa: ANN401 - injected for tests
                 clock: Any = None) -> None:  # noqa: ANN401
        self._url = url
        self._max_age = max_age
        self._fetch = fetch or _fetch_feed
        self._clock = clock or time.monotonic
        self._last = float("-inf")
        self._lock = asyncio.Lock()
        self._sessions: frozenset[str] = frozenset()
        self._members: dict[str, tuple[int, str]] = {}

    async def _fresh(self) -> None:
        if self._clock() - self._last < self._max_age:
            return
        async with self._lock:
            if self._clock() - self._last < self._max_age:
                return  # another request fetched while this one waited
            self._last = self._clock()
            try:
                feed = await self._fetch(self._url)
                sessions = frozenset(str(s) for s in feed.get("sessions", []))
                members = {str(m["key"]): (int(m["epoch"]), str(m.get("reason", "")))
                           for m in feed.get("members", [])}
            except Exception as exc:  # noqa: BLE001 - identity down is not our 500
                log.warning("revocation feed from %s failed: %s", self._url, exc)
                return
            self._sessions, self._members = sessions, members

    async def refusal(self, claims: Claims) -> str | None:
        """Why this token no longer speaks for its holder, in words; None
        when nothing identity revoked touches it."""
        await self._fresh()
        if claims.session_id and claims.session_id in self._sessions:
            return "this session was signed out — sign in again"
        held = self._members.get(member_key(claims.user_id, claims.org))
        if held is not None and claims.epoch < held[0]:
            if held[1] == "removed":
                return "you are no longer a member of this workspace"
            return "your role in this workspace changed — sign in again to continue"
        return None
