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

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Claims:
        try:
            return cls(user_id=payload["sub"], email=payload["eml"],
                       org=payload["org"], role=payload["role"],
                       tier=payload["tier"])
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
