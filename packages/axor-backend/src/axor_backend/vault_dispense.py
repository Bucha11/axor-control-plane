"""What a credential was fetched FOR — the attestation a dispense carries.

The vault knew who asked and for what (tool, endpoint) and nothing else. That
is enough to enforce scope and nothing more: a node-bound credential was a
standing licence to drain every secret in that node's scope, silently, with no
record tying any of it to work the node actually did.

So a dispense states the call it is for, and the plane records it. Three things
follow, and only the first is authentication:

* **The attested call must be the dispensed one.** `tool` and `endpoint` in the
  attestation must equal the ones being fetched, and `node_id` must be the node
  the request already proved it may speak for. An attestation for one call
  cannot fetch another call's credential.
* **A refused call gets no credential.** A caller that gates (the wrapped
  runtime) states the kernel's verdict; `deny` means the kernel already said no,
  and the vault does not overrule it in the permissive direction. A caller that
  does not gate — the observe-only proxy — states no verdict, and says so rather
  than claiming a `pass` it never computed.
* **Every dispense leaves a row.** The signing vault has had an audit since it
  shipped; the credential vault had none at all. Draining a scope now looks
  like what it is: dispenses with no run behind them.

The signature is optional and its absence is recorded, never assumed away. A
node with a registered pubkey signs the attestation (RFC 8785 canonical bytes,
ed25519 — the same envelope operator commands use); a node without one is
accepted and the row reads `signed: false`. It is not authentication — the
request already carried a node-bound credential, and a stolen key signs as
happily as it bears — it is non-repudiation: on a hosted deployment the customer
can verify their own dispense log against their own node's key without trusting
the backend that stored it.
"""
from __future__ import annotations

from typing import Any

from axor_backend.signing import jcs_canonical

NODE_KEYS_SETTING = "node_pubkeys/v1"
DISPENSE_LOG_SETTING = "vault_creds/dispense_log/v1"
DISPENSE_LOG_CAP = 1000

# Fields the attestation signs, in the envelope both sides rebuild. Anything not
# listed is not covered by the signature and must not be trusted as if it were.
ATTESTED_FIELDS = (
    "node_id", "tool", "endpoint", "timestamp",
    "run_id", "seq", "verdict", "causal_root",
)


class AttestationRefused(Exception):
    """The dispense was refused before any credential was read."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def signed_bytes(attestation: dict[str, Any]) -> bytes:
    """The exact bytes a node signs: the attested fields, JCS-canonical.

    Absent optional fields are absent from the envelope rather than present as
    null — a signer and a verifier that disagree about which of those two an
    unset `seq` is would produce different bytes for the same statement.
    """
    return jcs_canonical({
        k: attestation[k] for k in ATTESTED_FIELDS
        if attestation.get(k) is not None
    })


def verify(
    attestation: Any,  # noqa: ANN401 - arrives as untrusted JSON
    *,
    node_id: str,
    tool: str,
    endpoint: str,
    pubkey_hex: str | None,
) -> bool:
    """Check the attestation against the dispense it accompanies.

    Returns whether it was cryptographically signed. Raises
    :class:`AttestationRefused` for every reason the credential must not be
    handed over.
    """
    if not isinstance(attestation, dict):
        raise AttestationRefused(
            "dispense requires an `attestation` naming the call the credential "
            "is for"
        )
    for field in ("node_id", "tool", "endpoint", "timestamp"):
        if not attestation.get(field):
            raise AttestationRefused(f"attestation is missing {field}")
    if str(attestation["node_id"]) != node_id:
        raise AttestationRefused(
            f"attestation is for node {attestation['node_id']!r}, "
            f"but the credential was requested for {node_id!r}"
        )
    if str(attestation["tool"]) != tool or str(attestation["endpoint"]) != endpoint:
        raise AttestationRefused(
            "attestation names a different call than the credential requested"
        )
    verdict = attestation.get("verdict")
    if verdict is not None and str(verdict).lower() == "deny":
        raise AttestationRefused(
            "the kernel denied this call; the vault does not overrule a denial "
            "by handing over the credential anyway"
        )
    if pubkey_hex is None:
        # No key registered for this node: accepted, and the row will say the
        # attestation was unsigned. Silence here is what would make an unsigned
        # dispense indistinguishable from a signed one in the log.
        return False
    if not _verify_ed25519(pubkey_hex, signed_bytes(attestation),
                           str(attestation.get("sig", ""))):
        raise AttestationRefused(
            f"attestation signature does not verify against the registered "
            f"pubkey for node {node_id!r}"
        )
    return True


def _verify_ed25519(pubkey_hex: str, message: bytes, sig_hex: str) -> bool:
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey

        VerifyKey(bytes.fromhex(pubkey_hex)).verify(message, bytes.fromhex(sig_hex))
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


def log_entry(
    attestation: dict[str, Any], *, version: int, signed: bool, principal: str,
    ts: str,
) -> dict[str, Any]:
    """One row of the dispense log. Carries what the credential was for and who
    asked — never the credential."""
    return {
        "node_id": str(attestation["node_id"]),
        "tool": str(attestation["tool"]),
        "endpoint": str(attestation["endpoint"]),
        "version": version,
        "run_id": attestation.get("run_id"),
        "seq": attestation.get("seq"),
        "verdict": attestation.get("verdict"),
        "causal_root": attestation.get("causal_root"),
        "signed": signed,
        # The credential that arrived, beside what the attestation claimed —
        # the same pairing the signing vault's audit makes.
        "principal": principal,
        "attested_ts": str(attestation["timestamp"]),
        "ts": ts,
    }
