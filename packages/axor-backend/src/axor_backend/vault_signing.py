"""Operator signing-key custody (spec v2 Ch.5 §2) — the vault SIGNS, never
surrenders.

For tool creds the vault hands out a secret; for signing keys the operation is
inverted: ``sign(key_id, payload) → signature`` and the private key never
leaves — not to a node, not to the plane, not to the operator's browser. This
preserves the property protocol §6 depends on: a compromised caller can only
obtain signatures over payloads it submits — it cannot exfiltrate the key to
sign offline or impersonate the federation elsewhere.

Authorization to sign is a scoped, audited capability: which operators may
request signatures for which key is config; every request is logged (who, which
key, payload hash) — a signature is an operator action and belongs in the audit
trail beside the command it authorizes (§12.3).

"Every request" had two holes. A request naming a key that does not exist left
no trace at all, so probing key ids was the one way to touch this surface
invisibly, while a wrong-operator attempt was recorded. And the log was a blob
loaded and stored back, so twenty-five concurrent signatures left ONE row for
twenty-five issued signatures — the log is appended through
``Store.mutate_setting`` now, one atomic read-modify-write.

"Who" is recorded twice on purpose. ``operator`` is what the request CLAIMS,
and the vault can only check it against the key's allowlist — naming an
authorized operator is not being one. ``principal`` is the credential that
actually arrived. An audit trail that prints only the claim answers a question
it does not know the answer to.

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
        the operator pins it in adapter config; the private seed stays here.

        The existence check and the write are one atomic mutation: read-then-
        write meant two operators creating the same key id both succeeded, and
        the second overwrote the first — replacing a key already pinned in
        adapter config, so every command signed with it started failing
        verification at the node.
        """
        if not key_id:
            raise SignRefused("a key needs an id")
        authorized = sorted({o for o in operators if o})
        if not authorized:
            raise SignRefused(
                f"key {key_id!r} needs at least one authorized operator — "
                "a key nobody may sign with is not custody, it is a dead entry"
            )
        created = datetime.now(UTC).isoformat()
        # Generated OUTSIDE the mutation: it is retried on contention, and a
        # closure that minted fresh key material on each pass would decide from
        # something other than what it was handed.
        key = SigningKey.generate()
        fresh = {
            # custody-internal, never in responses
            "seed_hex": key.encode().hex(),
            "public_key_hex": key.verify_key.encode().hex(),
            "operators": authorized,
            "created_ts": created,
        }

        def mutate(stored: Any) -> dict[str, dict]:  # noqa: ANN401
            keys = dict(stored or {})
            if key_id in keys:
                raise SignRefused(
                    f"key {key_id!r} already exists (rotation is a new key)"
                )
            keys[key_id] = fresh
            return keys

        keys = await self._store.mutate_setting(_KEYS_KEY, mutate)
        return {
            "key_id": key_id,
            "public_key_hex": keys[key_id]["public_key_hex"],
            "operators": keys[key_id]["operators"],
        }

    async def sign(
        self, operator: str, key_id: str, payload: bytes, principal: str = "",
    ) -> dict:
        """Delegated signing: authorized operators get a signature over the
        payload they submit — and an audit entry, unconditionally.

        Unconditionally means the refusals too, including the unknown key: it
        was the one way to touch this surface without leaving a row, which made
        key-id probing the quiet path into a custody service.
        """
        keys = await self._keys()
        entry = keys.get(key_id)
        if entry is None:
            await self._audit(operator, key_id, payload, False, principal,
                              refusal="unknown key")
            raise SignRefused(f"unknown key {key_id!r}")
        if operator not in entry["operators"]:
            await self._audit(operator, key_id, payload, False, principal,
                              refusal="operator not authorized for this key")
            raise SignRefused(
                f"operator {operator!r} is not authorized to request "
                f"signatures for {key_id!r}"
            )
        sig = SigningKey(bytes.fromhex(entry["seed_hex"])).sign(payload).signature
        await self._audit(operator, key_id, payload, True, principal)
        return {"signature_hex": sig.hex(), "key_id": key_id}

    async def _audit(
        self, operator: str, key_id: str, payload: bytes, granted: bool,
        principal: str = "", refusal: str = "",
    ) -> None:
        entry = {
            # What the request claimed…
            "operator": operator,
            # …and the credential that actually arrived. Empty in the open dev
            # posture, where there is no principal to name.
            "principal": principal,
            "key_id": key_id,
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "granted": granted,
            "ts": datetime.now(UTC).isoformat(),
        }
        if refusal:
            entry["refusal"] = refusal

        def mutate(stored: Any) -> list[dict]:  # noqa: ANN401
            return [*(stored or []), entry][-_AUDIT_CAP:]

        await self._store.mutate_setting(_AUDIT_KEY, mutate)

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
