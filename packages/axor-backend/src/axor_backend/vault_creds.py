"""Federation tool-credential vault (spec v2 Ch.5 §1) — the DISPENSE side.

One vault holds tool credentials for all nodes in the federation; a node
fetches by (tool, endpoint) at call time and the proxy injects it at the sink
(``axor_proxy.vault``). Federation-wide
STORAGE does not mean federation-wide ACCESS: enrollment scope is per node,
checked at dispense — a compromised web-scraper cannot pull the payments
credential just because both live in the same vault. Shared storage,
partitioned access.

Fail closed, federation-wide (§14.2 decision #14): vault down or credential
missing → a typed denial at every node. No cached creds anywhere, ever.
Rotation is versioned config; the plane may REVOKE federation-wide in an
incident (narrowing), never grant.

THE WALL (Ch.5 §3): this module must never import, call, or share credentials
with :mod:`axor_backend.vault_signing`. They are two subsystems that happen to
share the word "vault"; a single admin surface spanning both would recreate
the single-point-of-forgery the design avoids. CI enforces the import wall.

ENVELOPE MODE (§14.2 self-hosted-first, and the condition it names for hosted):
register a sealing PUBLIC key and the vault stops holding plaintext. Enrollment
then carries a `sealed_secret` — sealed to that key by whoever has the secret —
and this module stores and returns an opaque string it cannot open. The private
half lives with the nodes; the backend needs no crypto for this at all, which is
the point: it is not trusted to decline to look, it is unable to.

The rule is enforced, not offered: once a sealing key is registered, a plaintext
`secret` is REFUSED. A mode you can silently fall out of is not a custody
boundary.

Persistence: the settings KV (versioned entries), mutated through
``Store.mutate_setting`` so a change is one atomic read-modify-write. It was
``get_setting`` then ``set_setting`` — two transactions with the whole blob in
between — and concurrent operations erased each other: twenty concurrent
enrollments left one credential, and a revoke racing a rotate left the
credential live after an operator had been told it was revoked. A production
deployment plugs a real secret store behind the same interface; the
dispense/scope/fail-closed semantics — the part that carries the security
property — do not change with the backend.
"""
from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_STORE_KEY = "vault_creds/v1"


class DispenseDenied(Exception):
    """Typed denial: scope mismatch, revoked, or vault unavailable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class NotEnrolled(DispenseDenied):
    """Nothing is enrolled for this (tool, endpoint) — a 404, not a refusal.

    A subclass so the dispense path, where "nothing enrolled" IS the typed
    denial (fail closed), keeps catching it; rotate and revoke separate it
    because asking to change something that does not exist is a different
    answer from being refused something that does.
    """


class EnrollmentInvalid(Exception):
    """An enrollment the vault will not store, with the reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Where a dispensed credential goes in the outgoing request. "Inject at the
# sink" is not a complete instruction without it: the vault has to say WHICH
# header carries the secret and what precedes it. Defaults are the common case
# (`Authorization: Bearer <secret>`); an API-key tool enrolls with its own
# header and an empty scheme.
DEFAULT_HEADER = "Authorization"
DEFAULT_SCHEME = "Bearer"

# The org's credential-sealing PUBLIC key (X25519, hex). Public by definition —
# what it buys is that this deployment can no longer store plaintext, not that
# the key is a secret.
SEALING_KEY_SETTING = "vault_creds/sealing_key/v1"

# A libsodium sealed box is 48 bytes of overhead plus the message, so anything
# shorter than the overhead is not one. Checked because an enrollment that
# stored a truncated or empty "ciphertext" would fail at the sink, at call time,
# on the node — the furthest possible point from where the mistake was made.
_MIN_SEALED_BYTES = 48

# RFC 9110 field names: visible ASCII, no separators. Checked at enrollment
# rather than trusted, because this string is written into a request the proxy
# builds — a header name carrying CR/LF would let an enrollment smuggle
# additional headers into every call the credential is injected into.
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


@dataclass(frozen=True)
class Enrollment:
    tool: str
    endpoint: str
    secret: str
    version: int
    revoked: bool
    scope_nodes: tuple[str, ...]  # node ids allowed to dispense this credential
    header: str = DEFAULT_HEADER
    scheme: str = DEFAULT_SCHEME


def _key(tool: str, endpoint: str) -> str:
    return f"{tool}\x1f{endpoint}"


class ToolCredentialVault:
    """Federation-scoped credential custody with per-node dispense scope."""

    def __init__(self, store: Any) -> None:  # noqa: ANN401 - Store protocol
        self._store = store

    async def _load(self) -> dict[str, dict]:
        return (await self._store.get_setting(_STORE_KEY)) or {}

    async def _mutate(self, change: Callable[[dict[str, dict]], Any]) -> Any:  # noqa: ANN401
        """Apply `change` to the credential map inside one atomic write.

        `change` may raise a vault exception to refuse; nothing is stored then.
        It is re-run on contention, so it must decide only from the map it is
        handed — never from a value read before the call.
        """
        out: list[Any] = []

        def mutate(stored: Any) -> dict[str, dict]:  # noqa: ANN401
            data = dict(stored or {})
            out.clear()
            out.append(change(data))
            return data

        await self._store.mutate_setting(_STORE_KEY, mutate)
        return out[0]

    async def sealing_key(self) -> str | None:
        """The org's sealing pubkey, or None when this deployment stores
        plaintext (the dev backend that decision #13 allows)."""
        return (await self._store.get_setting(SEALING_KEY_SETTING)) or None

    async def enroll(
        self, tool: str, endpoint: str, secret: str, scope_nodes: list[str],
        header: str = DEFAULT_HEADER, scheme: str = DEFAULT_SCHEME,
        sealed_secret: str = "",
    ) -> dict:
        """The grant path (the plane may narrow, never grant), and the only one.

        Validated rather than stored as given: an enrollment with no tool, no
        endpoint or no secret is not a credential, and one with an empty scope
        is a credential no node may ever fetch — silently useless, which is the
        one thing a fail-closed vault must not be quiet about.
        """
        if not tool or not endpoint:
            raise EnrollmentInvalid("enrollment requires both a tool and an endpoint")
        sealing = await self.sealing_key()
        if sealing and secret:
            raise EnrollmentInvalid(
                "this deployment has a sealing key registered, so it does not "
                "store plaintext credentials — send `sealed_secret`, sealed to "
                f"{sealing[:16]}…, instead of `secret`"
            )
        if sealed_secret:
            _check_sealed(sealed_secret, tool, endpoint)
        elif not secret:
            raise EnrollmentInvalid(f"no secret given for ({tool}, {endpoint})")
        scope = sorted({n for n in scope_nodes if n})
        if not scope:
            raise EnrollmentInvalid(
                f"({tool}, {endpoint}) needs at least one node in scope_nodes — "
                "an empty scope is a credential no node may dispense"
            )
        header = header or DEFAULT_HEADER
        if not _HEADER_NAME.match(header):
            raise EnrollmentInvalid(
                f"{header!r} is not a valid header name — the injection header "
                "is written into every request this credential is injected into"
            )
        if any(c in scheme for c in "\r\n"):
            raise EnrollmentInvalid("the auth scheme may not contain CR or LF")

        def change(data: dict[str, dict]) -> dict:
            prior = data.get(_key(tool, endpoint))
            version = (prior["version"] + 1) if prior else 1
            entry = {
                "tool": tool, "endpoint": endpoint,
                "version": version, "revoked": False, "scope_nodes": scope,
                "header": header, "scheme": scheme,
            }
            # Exactly one of the two is stored. Keeping both would mean a
            # deployment that had switched to envelope mode still had the
            # plaintext of everything enrolled before it.
            if sealed_secret:
                entry["sealed_secret"] = sealed_secret
            else:
                entry["secret"] = secret
            data[_key(tool, endpoint)] = entry
            return {"tool": tool, "endpoint": endpoint, "version": version}

        return await self._mutate(change)

    async def dispense(self, node_id: str, tool: str, endpoint: str) -> dict:
        """The one read path. Scope is enforced HERE, at dispense — per node,
        per (tool, endpoint). Every failure is a typed denial (fail closed)."""
        data = await self._load()
        entry = data.get(_key(tool, endpoint))
        if entry is None:
            raise NotEnrolled(f"no credential enrolled for ({tool}, {endpoint})")
        if entry["revoked"]:
            raise DispenseDenied(f"credential for ({tool}, {endpoint}) is revoked")
        if node_id not in entry["scope_nodes"]:
            raise DispenseDenied(
                f"node {node_id!r} is not in the dispense scope for "
                f"({tool}, {endpoint}) — scope mismatch"
            )
        held = (
            {"sealed_secret": entry["sealed_secret"]} if entry.get("sealed_secret")
            else {"secret": entry.get("secret", "")}
        )
        return {
            **held,
            "version": entry["version"],
            # Placement travels WITH the secret: the caller injecting it must
            # not have to hold a second copy of the enrollment to know where it
            # goes, and a rotation that moves the header takes effect at the
            # next call rather than after a redeploy.
            "header": entry.get("header", DEFAULT_HEADER),
            "scheme": entry.get("scheme", DEFAULT_SCHEME),
        }

    async def rotate(
        self, tool: str, endpoint: str, new_secret: str, sealed_secret: str = "",
    ) -> dict:
        """New secret, same scope, same revocation state.

        Rotation used to clear `revoked`, silently. An operator who revoked a
        credential during an incident and then rotated its secret re-armed it
        federation-wide, and the answer — `{"version": 2}` — did not mention it.
        Un-revoking is granting, and granting goes through enrollment; that rule
        is the module's, and rotate was the one operation quietly breaking it.
        So a revoked credential refuses rotation and says what to do instead.
        """
        sealing = await self.sealing_key()
        if sealing and new_secret:
            raise EnrollmentInvalid(
                "this deployment has a sealing key registered — rotate with "
                "`sealed_secret`, not `secret`"
            )
        if sealed_secret:
            _check_sealed(sealed_secret, tool, endpoint)
        elif not new_secret:
            raise EnrollmentInvalid(f"no new secret given for ({tool}, {endpoint})")

        def change(data: dict[str, dict]) -> dict:
            entry = data.get(_key(tool, endpoint))
            if entry is None:
                raise NotEnrolled(f"nothing enrolled for ({tool}, {endpoint})")
            if entry["revoked"]:
                raise DispenseDenied(
                    f"({tool}, {endpoint}) is revoked; rotating would grant it "
                    "again. Re-enroll to grant — revocation is narrowing, and "
                    "undoing it is a deliberate act."
                )
            entry.pop("secret", None)
            entry.pop("sealed_secret", None)
            if sealed_secret:
                entry["sealed_secret"] = sealed_secret
            else:
                entry["secret"] = new_secret
            entry["version"] += 1
            return {"version": entry["version"]}

        return await self._mutate(change)

    async def revoke(self, tool: str, endpoint: str) -> dict:
        """Narrowing only: the plane may revoke in an incident; granting goes
        through enrollment config, never through a plane command."""
        def change(data: dict[str, dict]) -> dict:
            entry = data.get(_key(tool, endpoint))
            if entry is None:
                raise NotEnrolled(f"nothing enrolled for ({tool}, {endpoint})")
            entry["revoked"] = True
            return {"revoked": True, "version": entry["version"]}

        return await self._mutate(change)

    async def health(self) -> dict:
        data = await self._load()
        return {
            "enrolled": [
                {"tool": e["tool"], "endpoint": e["endpoint"],
                 "version": e["version"], "revoked": e["revoked"],
                 "scope_nodes": e["scope_nodes"],
                 "header": e.get("header", DEFAULT_HEADER),
                 "scheme": e.get("scheme", DEFAULT_SCHEME),
                 # Whether this deployment can read this credential at all.
                 "sealed": bool(e.get("sealed_secret"))}
                for e in data.values()
            ],
        }


def _check_sealed(sealed_secret: str, tool: str, endpoint: str) -> None:
    """A sealed secret this vault cannot open, it can still refuse to store
    garbage as. The alternative is failing at the sink, at call time, on the
    node — the furthest possible point from where the mistake was made."""
    try:
        raw = base64.b64decode(sealed_secret, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EnrollmentInvalid(
            f"sealed_secret for ({tool}, {endpoint}) is not valid base64: {exc}"
        ) from exc
    if len(raw) < _MIN_SEALED_BYTES:
        raise EnrollmentInvalid(
            f"sealed_secret for ({tool}, {endpoint}) is {len(raw)} bytes; a "
            f"sealed box is at least {_MIN_SEALED_BYTES}"
        )
