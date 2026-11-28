"""Operators' signing keys made in their browsers, and how the plane checks a
signature with them.

The browser makes an Ed25519 pair with WebCrypto, private half non-extractable
in IndexedDB: the plane never sees it, so a compromised plane cannot sign. The
plane keeps the public half bound to the account (``operator_keys``), checks
commands against it as against the deployment's keyring, and serves to
machines what they need to extend their trust:

* ``endorsed`` — keys an already-trusted operator signed for
  (``{"kind": "operator_key", "org", "operator", "pubkey"}``); a sidecar adds
  one only when the endorsement verifies with a key it already trusts;
* ``revoked`` — taken as the plane's word: dropping a key only narrows.
"""
from __future__ import annotations

from typing import Any

from axor_backend.signing import CommandRejected

OPERATOR_KIND = "operator_key"


def operator_id(email: str, pubkey: str) -> str:
    return f"{email.lower()}#{pubkey.lower()[:8]}"


def endorsement_payload(org: str, op_id: str, pubkey: str) -> dict[str, str]:
    return {"kind": OPERATOR_KIND, "org": org, "operator": op_id, "pubkey": pubkey.lower()}


def _verify_with(pubkey_hex: str, message: bytes, sig: Any) -> None:  # noqa: ANN401
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    if not isinstance(sig, str):
        raise CommandRejected("sig must be a hex string")
    try:
        VerifyKey(bytes.fromhex(pubkey_hex)).verify(message, bytes.fromhex(sig))
    except (BadSignatureError, ValueError) as exc:
        raise CommandRejected("sig_invalid") from exc


async def active_keys(store: Any) -> dict[str, str]:  # noqa: ANN401
    """operator id -> public key hex, browser keys not revoked."""
    return {k["operator_id"]: k["pubkey"] for k in await store.operator_keys_list()
            if not k["revoked_ts"]}


async def signing_required(state: Any, store: Any) -> bool:  # noqa: ANN401
    """Whether the plane asks commands for a signature: the deployment has an
    operator keyring, or this workspace registered a browser key."""
    return not state.keyring.empty or bool(await active_keys(store))


async def verify(state: Any, store: Any, operator: Any, message: bytes,  # noqa: ANN401
                 sig: Any) -> None:  # noqa: ANN401
    """Good signature by a browser key of this workspace or the deployment's
    keyring, or :class:`CommandRejected`."""
    if not isinstance(operator, str):
        raise CommandRejected("operator must be a string")
    keys = await store.operator_keys_list()
    key = next((k["pubkey"] for k in keys
                if k["operator_id"] == operator and not k["revoked_ts"]), None)
    if key is not None:
        _verify_with(key, message, sig)
        return
    if any(k["operator_id"] == operator for k in keys):
        # said as what it is: "unknown operator" sent its holder to register
        # a key the workspace had taken away on purpose (QA-173)
        raise CommandRejected(f"operator key {operator!r} was revoked")
    state.keyring.verify(operator, message, sig)


async def for_machines(store: Any) -> dict[str, Any]:  # noqa: ANN401
    """What a sidecar extends or narrows its trust by."""
    keys = await store.operator_keys_list()
    # a vouch is worth what its voucher is: one signed by a key since revoked
    # vouches for nothing (QA-173)
    revoked = {k["operator_id"] for k in keys if k["revoked_ts"]}
    return {
        "endorsed": [{"operator": k["operator_id"], "pubkey": k["pubkey"],
                      "endorsement": k["endorsement"]}
                     for k in keys if k["endorsement"] and not k["revoked_ts"]
                     and k["endorsement"].get("operator") not in revoked],
        "revoked": [k["operator_id"] for k in keys if k["revoked_ts"]],
    }
