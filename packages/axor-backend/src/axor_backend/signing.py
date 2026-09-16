"""Operator command signing (protocol v0.3, section 6).

Canonicalization is JCS (RFC 8785), restricted: signed payloads carry only
objects, arrays, strings, integers, booleans and null. Floats are rejected —
governance payloads have no business being IEEE-754, and refusing them keeps
this implementation trivially correct against the RFC's number-serialization
subtleties. Test vectors live in tests/vectors/jcs.json.

Verification here is defense in depth only: the adapter re-verifies with
operator pubkeys from ITS OWN config — a compromised backend must not be able
to forge commands.

That sentence was half true until v0.3. The adapter re-verified a DELTA; the
snapshot the desired-state stream opens with carried bare state and was applied
unchecked, so a compromised plane could forge anything by sending it that way
instead — and reconnect, which takes a fresh snapshot, is routine. Making it
true needed something of this side: the plane has to KEEP the signature a
command arrived with, per state key, so the snapshot can hand it back
(`storage.desired_commands`, `plane.desired_stream`). Verification is still not
this side's job; supplying the evidence for it is.
"""
from __future__ import annotations

from typing import Any

from axor_core.kernel import CanonicalizationError, canonicalize
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from axor_backend.env import EnvError
from axor_backend.errors import CommandRejected


def jcs_canonical(value: Any) -> bytes:  # noqa: ANN401 - arbitrary JSON input
    """RFC 8785 canonical form of a float-free JSON value.

    Delegates to the pure kernel canonicalizer (axor_core.kernel.jcs) — the one
    implementation the adapter also verifies against, so the two sides agree
    byte for byte (protocol §6). Float/non-string-key rejection is enforced
    there; we translate it to the backend's CommandRejected."""
    try:
        return canonicalize(value)
    except CanonicalizationError as exc:
        raise CommandRejected(str(exc)) from exc


def signed_payload(node_id: str, version: int, body: dict, timestamp: str) -> bytes:
    """The exact bytes an operator signs: (node_id, version, delta|fact, ts)."""
    return jcs_canonical(
        {"node_id": node_id, "version": version, "body": body, "timestamp": timestamp}
    )


class OperatorKeyring:
    """operator id -> ed25519 public key (hex). Loaded from backend config;
    the authoritative keyring is always the adapter's local one."""

    def __init__(self, keys: dict[str, str]) -> None:
        self._keys = {op: _public_key(op, hexkey) for op, hexkey in keys.items()}

    def verify(self, operator: str, message: bytes, sig_hex: str) -> None:
        """Good signature, or :class:`CommandRejected`. Never anything else.

        Everything reaching here came off the wire as JSON, so `operator` and
        `sig_hex` are whatever the caller put there — a number, a list, null.
        Catching only ValueError left those as TypeErrors escaping an
        AUTHENTICATION check as a bare `500 {"error": "internal"}`: the caller's
        malformed command reported as our fault, and "the signature did not
        verify" made indistinguishable from "the backend broke".
        """
        if not isinstance(operator, str):
            raise CommandRejected("operator must be a string")
        if not isinstance(sig_hex, str):
            raise CommandRejected("sig must be a hex string")
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


def _public_key(operator: str, hexkey: Any) -> VerifyKey:  # noqa: ANN401 - env JSON
    """One operator's key, or an EnvError naming WHICH one.

    `EnvError`, not `ConfigInvalid`: that one is for a malformed KERNEL config
    an operator typed into a text box, and answers 400. This is a deployment
    setting read at boot, and its contract is the one `env.py` states — a
    configured value the process cannot use, named, and the process does not
    start.

    The keyring is built inside `create_app`, so a bad key is a backend that
    does not start — and it used to not start with a raw
    ``ValueError: non-hexadecimal number found in fromhex() arg at position 0``,
    which names neither the variable nor the operator. `.env.example`'s own
    third hardening step is to paste `AXOR_OPERATOR_KEYS={"op_you":
    "<ed25519-pubkey-hex>"}`, so following it literally and forgetting to
    substitute the key produced exactly that, with a character position for a
    diagnosis.
    """
    if not isinstance(hexkey, str):
        raise EnvError(
            f"AXOR_OPERATOR_KEYS: operator {operator!r} has a "
            f"{type(hexkey).__name__} where an ed25519 public key (64 hex "
            f"characters) belongs"
        )
    try:
        raw = bytes.fromhex(hexkey)
    except ValueError as exc:
        raise EnvError(
            f"AXOR_OPERATOR_KEYS: operator {operator!r} has a key that is not "
            f"hex ({exc}). It is the 32-byte ed25519 PUBLIC key as 64 hex "
            f"characters; see .env.example and SECURITY.md."
        ) from exc
    if len(raw) != 32:
        raise EnvError(
            f"AXOR_OPERATOR_KEYS: operator {operator!r} has a {len(raw)}-byte "
            f"key; an ed25519 public key is 32 bytes (64 hex characters). A "
            f"private seed or a PEM will not do — this is the public half."
        )
    return VerifyKey(raw)
