"""The signing key and its published JWKS.

axor-identity signs access tokens with EdDSA (Ed25519). It holds the private
key; every other service verifies tokens against the public key it publishes at
`/.well-known/jwks.json`. Asymmetric on purpose — verifiers never hold a secret
that could mint tokens, and there is no per-request call back to identity.
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _public_raw(public_key: Ed25519PublicKey) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def derive_kid(public_key: Ed25519PublicKey) -> str:
    """A stable key id from the public key, so the JWT header and the JWKS agree
    without configuration."""
    return _b64url(hashlib.sha256(_public_raw(public_key)).digest()[:12])


@dataclass(frozen=True)
class SigningKey:
    private_key: Ed25519PrivateKey
    kid: str

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()

    def jwk(self) -> dict[str, str]:
        return {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": _b64url(_public_raw(self.public_key)),
            "use": "sig",
            "alg": "EdDSA",
            "kid": self.kid,
        }

    def jwks(self) -> dict[str, list[dict[str, str]]]:
        return {"keys": [self.jwk()]}


def load_signing_key(pem: str | None = None, kid: str | None = None) -> SigningKey:
    """Load an Ed25519 private key from a PKCS#8 PEM, or generate one.

    Generating is for dev/tests only: the key dies with the process, so tokens
    it signed can't be verified by any other instance. Production sets
    AXOR_IDENTITY_SIGNING_KEY.
    """
    pem = pem if pem is not None else os.environ.get("AXOR_IDENTITY_SIGNING_KEY")
    if pem:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("AXOR_IDENTITY_SIGNING_KEY must be an Ed25519 private key")
    else:
        key = Ed25519PrivateKey.generate()
    kid = kid or os.environ.get("AXOR_IDENTITY_KID") or derive_kid(key.public_key())
    return SigningKey(private_key=key, kid=kid)


def generate_pem() -> str:
    """A fresh Ed25519 private key as a PKCS#8 PEM — a helper for operators
    provisioning AXOR_IDENTITY_SIGNING_KEY."""
    key = Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
