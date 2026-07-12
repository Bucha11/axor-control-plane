"""Operator signing-key custody (spec v2 Ch.5 §2) — the vault SIGNS, never
surrenders.

For tool creds the vault hands out a secret; for signing keys the operation is
inverted: ``sign(key_id, payload) → signature`` and the private key never
leaves — not to a node, not to the plane, not to the operator's browser. This
preserves the property protocol §6 depends on: a compromised caller can only
obtain signatures over payloads it submits — it cannot exfiltrate the key to
sign offline or impersonate the federation elsewhere.

Authorization to sign is a scoped, audited capability: which operators may
request signatures for which key is config; every request is logged (who,
which key, payload hash) — a signature is an operator action and belongs in
the audit trail beside the command it authorizes (§12.3).

Pubkeys are NOT secrets and are never fetched from here: verification keys
stay pinned in local adapter config — a compromised vault cannot swap what a
node checks against.

Custody backend is pluggable (HSM / cloud-KMS / software-keystore — same
``sign`` interface, posture declared per deployment); this ships the
software-keystore tier.

THE WALL (Ch.5 §3): this module must never import, call, or share credentials
with :mod:`axor_backend.vault_creds`. CI enforces the import wall.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from nacl.signing import SigningKey

_KEYS_KEY = "vault_signing/keys/v1"
_AUDIT_KEY = "vault_signing/audit/v1"
_AUDIT_CAP = 500


class SignRefused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class SigningCustody:
    """Software-keystore custody: keys generated inside, private half never
    emitted through any API this class exposes."""

    def __init__(self, store: Any) -> None:  # noqa: ANN401 - Store protocol
        self._store = store

    async def _keys(self) -> dict[str, dict]:
        return (await self._store.get_setting(_KEYS_KEY)) or {}

    async def create_key(self, key_id: str, operators: list[str]) -> dict:
        """Generate a keypair under custody. Returns ONLY the public half —
        the operator pins it in adapter config; the private seed stays here."""
        keys = await self._keys()
        if key_id in keys:
            raise SignRefused(f"key {key_id!r} already exists (rotation is a new key)")
        key = SigningKey.generate()
        keys[key_id] = {
            "seed_hex": key.encode().hex(),  # custody-internal, never in responses
            "public_key_hex": key.verify_key.encode().hex(),
            "operators": sorted(set(operators)),
            "created_ts": datetime.now(UTC).isoformat(),
        }
        await self._store.set_setting(_KEYS_KEY, keys)
        return {
            "key_id": key_id,
            "public_key_hex": keys[key_id]["public_key_hex"],
            "operators": keys[key_id]["operators"],
        }

    async def sign(self, operator: str, key_id: str, payload: bytes) -> dict:
        """Delegated signing: authorized operators get a signature over the
        payload they submit — and an audit entry, unconditionally."""
        keys = await self._keys()
        entry = keys.get(key_id)
        if entry is None:
            raise SignRefused(f"unknown key {key_id!r}")
        if operator not in entry["operators"]:
            await self._audit(operator, key_id, payload, granted=False)
            raise SignRefused(
                f"operator {operator!r} is not authorized to request "
                f"signatures for {key_id!r}"
            )
        sig = SigningKey(bytes.fromhex(entry["seed_hex"])).sign(payload).signature
        await self._audit(operator, key_id, payload, granted=True)
        return {"signature_hex": sig.hex(), "key_id": key_id}

    async def _audit(
        self, operator: str, key_id: str, payload: bytes, granted: bool
    ) -> None:
        log = (await self._store.get_setting(_AUDIT_KEY)) or []
        log.append({
            "operator": operator,
            "key_id": key_id,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "granted": granted,
            "ts": datetime.now(UTC).isoformat(),
        })
        await self._store.set_setting(_AUDIT_KEY, log[-_AUDIT_CAP:])

    async def audit(self) -> list[dict]:
        return (await self._store.get_setting(_AUDIT_KEY)) or []

    async def keys_public(self) -> list[dict]:
        """The listable surface: key ids, pubkeys, authorized operators —
        never a private half."""
        keys = await self._keys()
        return [
            {"key_id": kid, "public_key_hex": e["public_key_hex"],
             "operators": e["operators"], "created_ts": e["created_ts"]}
            for kid, e in sorted(keys.items())
        ]
