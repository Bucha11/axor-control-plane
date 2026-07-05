"""Operator command signing (protocol v0.2, section 6).

Canonicalization is JCS (RFC 8785), restricted: signed payloads carry only
objects, arrays, strings, integers, booleans and null. Floats are rejected —
governance payloads have no business being IEEE-754, and refusing them keeps
this implementation trivially correct against the RFC's number-serialization
subtleties. Test vectors live in tests/vectors/jcs.json.

Verification here is defense in depth only: the adapter re-verifies with
operator pubkeys from ITS OWN config — a compromised backend must not be able
to forge commands.
"""
from __future__ import annotations

import json
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from axor_backend.errors import CommandRejected


def jcs_canonical(value: Any) -> bytes:  # noqa: ANN401 - arbitrary JSON input
    """RFC 8785 canonical form of a float-free JSON value."""
    _reject_floats(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _reject_floats(value: object) -> None:
    if isinstance(value, float):
        raise CommandRejected("signed payloads must not contain floats (JCS subset)")
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise CommandRejected("signed payload keys must be strings")
            _reject_floats(v)
    elif isinstance(value, list):
        for v in value:
            _reject_floats(v)


def signed_payload(node_id: str, version: int, body: dict, timestamp: str) -> bytes:
    """The exact bytes an operator signs: (node_id, version, delta|fact, ts)."""
    return jcs_canonical(
        {"node_id": node_id, "version": version, "body": body, "timestamp": timestamp}
    )


class OperatorKeyring:
    """operator id -> ed25519 public key (hex). Loaded from backend config;
    the authoritative keyring is always the adapter's local one."""

    def __init__(self, keys: dict[str, str]) -> None:
        self._keys = {op: VerifyKey(bytes.fromhex(hexkey)) for op, hexkey in keys.items()}

    def verify(self, operator: str, message: bytes, sig_hex: str) -> None:
        key = self._keys.get(operator)
        if key is None:
            raise CommandRejected(f"unknown operator {operator!r}")
        try:
            key.verify(message, bytes.fromhex(sig_hex))
        except (BadSignatureError, ValueError) as exc:
            raise CommandRejected("sig_invalid") from exc

    @property
    def empty(self) -> bool:
        return not self._keys
