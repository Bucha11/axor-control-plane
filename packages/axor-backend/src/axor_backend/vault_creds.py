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

    async def enroll(
        self, tool: str, endpoint: str, secret: str, scope_nodes: list[str],
        header: str = DEFAULT_HEADER, scheme: str = DEFAULT_SCHEME,
    ) -> dict:
        """The grant path (the plane may narrow, never grant), and the only one.

        Validated rather than stored as given: an enrollment with no tool, no
        endpoint or no secret is not a credential, and one with an empty scope
        is a credential no node may ever fetch — silently useless, which is the
        one thing a fail-closed vault must not be quiet about.
        """
        if not tool or not endpoint:
            raise EnrollmentInvalid("enrollment requires both a tool and an endpoint")
        if not secret:
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
            data[_key(tool, endpoint)] = {
                "tool": tool, "endpoint": endpoint, "secret": secret,
                "version": version, "revoked": False, "scope_nodes": scope,
                "header": header, "scheme": scheme,
            }
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
        return {
            "secret": entry["secret"],
            "version": entry["version"],
            # Placement travels WITH the secret: the caller injecting it must
            # not have to hold a second copy of the enrollment to know where it
            # goes, and a rotation that moves the header takes effect at the
            # next call rather than after a redeploy.
            "header": entry.get("header", DEFAULT_HEADER),
            "scheme": entry.get("scheme", DEFAULT_SCHEME),
        }

    async def rotate(self, tool: str, endpoint: str, new_secret: str) -> dict:
        """New secret, same scope, same revocation state.

        Rotation used to clear `revoked`, silently. An operator who revoked a
        credential during an incident and then rotated its secret re-armed it
        federation-wide, and the answer — `{"version": 2}` — did not mention it.
        Un-revoking is granting, and granting goes through enrollment; that rule
        is the module's, and rotate was the one operation quietly breaking it.
        So a revoked credential refuses rotation and says what to do instead.
        """
        if not new_secret:
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
                 "scheme": e.get("scheme", DEFAULT_SCHEME)}
                for e in data.values()
            ],
        }
