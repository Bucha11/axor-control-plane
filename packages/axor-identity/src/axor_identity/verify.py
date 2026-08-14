"""Access-token verification — the SHARED client the Lab and control-plane use.

This is the one piece of axor-identity that runs *inside* other services. It
depends only on pyjwt[crypto] + cryptography (no FastAPI, no DB), so it vendors
cleanly. Given a JWKS (fetched once from `/.well-known/jwks.json` and cached),
it validates an access token's signature, issuer, and expiry, and returns the
claims as a typed object. It never holds a signing secret.

Downstream services map the returned claims to their own model:
  Lab → org == workspace, role → RBAC;  control-plane → org == tenant scope.
"""
from __future__ import annotations

import base64
import json
import urllib.request
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ISSUER = "axor-identity"
ALGORITHM = "EdDSA"


class IdentityError(Exception):
    """An access token was missing, malformed, expired, or not trusted."""


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


def _public_key_from_jwk(jwk: dict[str, str]) -> Ed25519PublicKey:
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise IdentityError("unsupported JWK: expected an Ed25519 OKP key")
    return Ed25519PublicKey.from_public_bytes(_b64url_decode(jwk["x"]))


def verify_access_token(token: str, jwks: dict[str, Any], *, issuer: str = ISSUER,
                        leeway: int = 30) -> Claims:
    """Validate `token` against `jwks` and return its Claims, or raise
    IdentityError. `jwks` is the parsed `/.well-known/jwks.json` document."""
    keys = {k.get("kid"): k for k in jwks.get("keys", [])}
    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise IdentityError(f"malformed token: {exc}") from exc
    jwk = keys.get(kid) or (jwks["keys"][0] if len(jwks.get("keys", [])) == 1 else None)
    if jwk is None:
        raise IdentityError(f"no verifying key for kid {kid!r}")
    try:
        payload = jwt.decode(
            token, _public_key_from_jwk(jwk), algorithms=[ALGORITHM],
            issuer=issuer, leeway=leeway,
            options={"require": ["exp", "iss", "sub"]})
    except jwt.PyJWTError as exc:
        raise IdentityError(str(exc)) from exc
    return Claims.from_payload(payload)


def fetch_jwks(url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Fetch a JWKS document over HTTP (std-lib only). Services fetch once and
    cache; the key rotates rarely and the kid tells them when to refetch."""
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read())
