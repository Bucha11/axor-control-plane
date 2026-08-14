"""Minting session tokens.

Two tokens per login:

- an **access** JWT (EdDSA), short-lived, carrying the claims a downstream
  service needs to authorize a request without calling back here;
- a **refresh** token, opaque and high-entropy, stored only as a SHA-256 hash,
  which `/v1/refresh` exchanges (and rotates) for a new access token.
"""
from __future__ import annotations

import hashlib
import secrets
import time

import jwt

from axor_identity.keys import SigningKey

ISSUER = "axor-identity"
ALGORITHM = "EdDSA"


def issue_access(signing_key: SigningKey, *, user_id: str, email: str, org: str,
                 role: str, tier: str, ttl: int, now: int | None = None) -> str:
    issued = int(time.time()) if now is None else now
    claims = {
        "iss": ISSUER,
        "sub": user_id,
        "eml": email,
        "org": org,
        "role": role,
        "tier": tier,
        "iat": issued,
        "exp": issued + ttl,
    }
    return jwt.encode(claims, signing_key.private_key, algorithm=ALGORITHM,
                      headers={"kid": signing_key.kid})


def new_refresh() -> tuple[str, str]:
    """Return (raw_token, token_hash). The raw token is returned to the client
    once; only its hash is persisted."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh(raw)


def hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
