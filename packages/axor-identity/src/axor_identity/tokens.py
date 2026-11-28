"""Minting session tokens.

Two tokens per login:

- an **access** JWT (EdDSA), short-lived, carrying the claims a downstream
  service needs to authorize a request without calling back here;
- a **refresh** token, opaque and high-entropy, stored only as a SHA-256 hash,
  which `/v1/refresh` exchanges (and rotates) for a new access token.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

import jwt

from axor_identity.keys import SigningKey

ISSUER = "axor-identity"
ALGORITHM = "EdDSA"


def issue_access(signing_key: SigningKey, *, user_id: str, email: str, org: str,
                 role: str, tier: str, ttl: int, now: int | None = None,
                 email_verified: bool = False, session_id: str | None = None,
                 epoch: int = 0) -> str:
    issued = int(time.time()) if now is None else now
    claims = {
        "iss": ISSUER,
        "sub": user_id,
        "eml": email,
        "org": org,
        "role": role,
        "tier": tier,
        # the address is proven theirs — downstream services gate mail on it
        "evf": bool(email_verified),
        "iat": issued,
        "exp": issued + ttl,
        # which membership epoch it was issued at: a removal or a role change
        # bumps it, and the control plane refuses a token below the current
        # one (QA-154; see /v1/revocations)
        "ep": epoch,
    }
    if session_id:
        # the sign-in it belongs to, so logging out ends this token too (QA-240)
        claims["sid"] = session_id
    return jwt.encode(claims, signing_key.private_key, algorithm=ALGORITHM,
                      headers={"kid": signing_key.kid})


def new_refresh() -> tuple[str, str]:
    """Return (raw_token, token_hash). The raw token is returned to the client
    once; only its hash is persisted."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh(raw)


def hash_refresh(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def successor_refresh(secret: bytes, raw: str) -> str:
    """The refresh token that replaces ``raw`` when it is rotated. Derived, not
    random, so presenting ``raw`` again a moment later (a duplicated tab holds a
    copy — QA-480) gets the SAME successor back rather than a second live one.
    Keyed by a server secret: holding ``raw`` alone does not give it."""
    mac = hmac.new(secret, b"axor-refresh-successor\0" + raw.encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()).rstrip(b"=").decode()
