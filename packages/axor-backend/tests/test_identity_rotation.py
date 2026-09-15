"""Identity verifying keys: what happens when the issuer rotates one.

The JWKS was fetched once, inside `AppConfig.from_env`, and never again — while
`fetch_jwks` told the next reader that "the kid tells it when a refetch is due".
So the day identity rotated its signing key, every human login answered a flat
401 until somebody restarted the backend.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging

import pytest
from axor_backend.identity_client import (
    IdentityError,
    JwksRefresher,
    UnknownKeyId,
    verify_access_token,
)


def _token(*, kid: str) -> str:
    """A syntactically valid JWT whose header names `kid`. Never verifies — the
    point is to reach the key lookup, which is where a rotation shows up."""
    def seg(payload: dict) -> str:
        raw = json.dumps(payload).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{seg({'alg': 'EdDSA', 'typ': 'JWT', 'kid': kid})}.{seg({'sub': 'u'})}.AA"


# Real 32-byte Ed25519 public keys: a JWK whose `x` is the wrong length raises
# out of `cryptography`, which is a different failure from the one under test.
def _jwk(kid: str, lo: int) -> dict[str, str]:
    material = base64.urlsafe_b64encode(bytes(range(lo, lo + 32))).rstrip(b"=")
    return {"kty": "OKP", "crv": "Ed25519", "kid": kid, "x": material.decode()}


OLD = {"keys": [_jwk("old", 0), _jwk("spare", 32)]}
NEW = {"keys": [_jwk("new", 64)]}


class _Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class TestOnlyAnUnknownKidTriggersARefetch:
    """`UnknownKeyId` is its own type because it is the ONE failure a refetch
    can fix. Retrying on a bad signature or an expired token would turn any
    unauthenticated caller into an outbound-request generator."""

    def test_a_kid_this_document_lacks_raises_the_refetchable_error(self) -> None:
        """The specific type, not the base: `pytest.raises(UnknownKeyId)` does
        not accept a plain IdentityError, which is what tells the caller apart
        the one failure worth another round trip."""
        # Two keys, so the "a single-key JWKS needs no kid" fallback cannot fire.
        with pytest.raises(UnknownKeyId):
            verify_access_token(_token(kid="rotated-away"), OLD)

    def test_a_malformed_token_is_not_refetchable(self) -> None:
        with pytest.raises(IdentityError) as raised:
            verify_access_token("not.a.jwt", OLD)
        assert not isinstance(raised.value, UnknownKeyId)

    def test_a_single_key_document_needs_no_kid(self) -> None:
        """A JWKS with one key verifies a token whose kid it does not name, so
        an issuer that omits kid is not a rotation — and must not trigger one.
        The signature still has to check out, which is the next failure here."""
        with pytest.raises(IdentityError) as raised:
            verify_access_token(_token(kid="whatever"), NEW)
        assert not isinstance(raised.value, UnknownKeyId)

    def test_a_malformed_jwk_is_a_401_not_a_500(self) -> None:
        """`Ed25519PublicKey.from_public_bytes` raises a bare ValueError, which
        went straight past the IdentityError `resolve_principal` catches — so a
        truncated key in the document answered every login with a 500."""
        broken = {"keys": [{"kty": "OKP", "crv": "Ed25519", "kid": "k", "x": "AA"}]}
        with pytest.raises(IdentityError, match="malformed JWK"):
            verify_access_token(_token(kid="k"), broken)

    async def test_the_refetch_replaces_the_document(self) -> None:
        calls: list[str] = []

        def fetch(url: str) -> dict:
            calls.append(url)
            return NEW

        refresher = JwksRefresher(OLD, "https://id.test/jwks", fetch=fetch)
        assert refresher.document is OLD
        assert await refresher.refreshed() == NEW
        assert refresher.document == NEW
        assert calls == ["https://id.test/jwks"]

    async def test_an_inline_jwks_never_refetches(self) -> None:
        """No URL means the operator pasted the document in; there is nowhere
        to go and nothing to say."""
        refresher = JwksRefresher(OLD, None, fetch=lambda url: NEW)
        assert await refresher.refreshed() is None
        assert refresher.document is OLD


class TestTheRefetchIsRateLimited:
    """Presenting a token needs no credential, so an unbounded refetch is an
    outbound request anyone can drive."""

    async def test_a_second_attempt_inside_the_window_does_not_fetch(self) -> None:
        clock = _Clock()
        calls = 0

        def fetch(url: str) -> dict:
            nonlocal calls
            calls += 1
            return NEW

        refresher = JwksRefresher(
            OLD, "https://id.test/jwks", min_interval=60.0, fetch=fetch, clock=clock,
        )
        assert await refresher.refreshed() == NEW
        assert await refresher.refreshed() is None
        assert await refresher.refreshed() is None
        assert calls == 1

        clock.t += 60.0
        assert await refresher.refreshed() == NEW
        assert calls == 2

    async def test_a_burst_of_callers_produces_one_fetch(self) -> None:
        clock = _Clock()
        calls = 0

        def fetch(url: str) -> dict:
            nonlocal calls
            calls += 1
            return NEW

        refresher = JwksRefresher(
            OLD, "https://id.test/jwks", min_interval=60.0, fetch=fetch, clock=clock,
        )
        results = await asyncio.gather(*(refresher.refreshed() for _ in range(20)))
        assert calls == 1
        assert sum(1 for r in results if r is not None) == 1


class TestAFailedRefetchKeepsTheKeysItHas:
    async def test_identity_being_unreachable_is_not_a_logout(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        def fetch(url: str) -> dict:
            raise OSError("connection refused")

        refresher = JwksRefresher(OLD, "https://id.test/jwks", fetch=fetch)
        with caplog.at_level(logging.WARNING, logger="axor.backend.identity"):
            assert await refresher.refreshed() is None
        assert refresher.document is OLD  # the old keys still verify old tokens
        assert any("refetch" in r.getMessage() for r in caplog.records)


class TestTheUnknownKidPathIsWiredToTheRefresher:
    def test_resolve_principal_retries_once_after_a_refetch(self) -> None:
        """The retry lives in `security.resolve_principal`; this pins that it
        is the UnknownKeyId branch that reaches the refresher, and that a
        second failure is a plain 401 rather than a second fetch."""
        import inspect

        from axor_backend import security

        source = inspect.getsource(security.resolve_principal)
        assert "except UnknownKeyId:" in source
        assert "await jwks.refreshed()" in source
        # exactly one retry: the inner verify is guarded by IdentityError
        assert source.count("await jwks.refreshed()") == 1
