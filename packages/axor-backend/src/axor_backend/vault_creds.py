"""Federation tool-credential vault (spec v2 Ch.5 §1) — the DISPENSE side.

One vault holds tool credentials for all nodes in the federation; a node
fetches by (tool, endpoint) at call time, injected at the sink. Federation-wide
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

Persistence: the settings KV (versioned entries). A production deployment
plugs a real secret store behind the same interface; the dispense/scope/
fail-closed semantics — the part that carries the security property — do not
change with the backend.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_STORE_KEY = "vault_creds/v1"


class DispenseDenied(Exception):
    """Typed denial: scope mismatch, revoked, or vault unavailable."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Enrollment:
    tool: str
    endpoint: str
    secret: str
    version: int
    revoked: bool
    scope_nodes: tuple[str, ...]  # node ids allowed to dispense this credential


def _key(tool: str, endpoint: str) -> str:
    return f"{tool}\x1f{endpoint}"


class ToolCredentialVault:
    """Federation-scoped credential custody with per-node dispense scope."""

    def __init__(self, store: Any) -> None:  # noqa: ANN401 - Store protocol
        self._store = store

    async def _load(self) -> dict[str, dict]:
        return (await self._store.get_setting(_STORE_KEY)) or {}

    async def _save(self, data: dict[str, dict]) -> None:
        await self._store.set_setting(_STORE_KEY, data)

    async def enroll(
        self, tool: str, endpoint: str, secret: str, scope_nodes: list[str]
    ) -> dict:
        data = await self._load()
        prior = data.get(_key(tool, endpoint))
        version = (prior["version"] + 1) if prior else 1
        data[_key(tool, endpoint)] = {
            "tool": tool, "endpoint": endpoint, "secret": secret,
            "version": version, "revoked": False,
            "scope_nodes": sorted(set(scope_nodes)),
        }
        await self._save(data)
        return {"tool": tool, "endpoint": endpoint, "version": version}

    async def dispense(self, node_id: str, tool: str, endpoint: str) -> dict:
        """The one read path. Scope is enforced HERE, at dispense — per node,
        per (tool, endpoint). Every failure is a typed denial (fail closed)."""
        data = await self._load()
        entry = data.get(_key(tool, endpoint))
        if entry is None:
            raise DispenseDenied(f"no credential enrolled for ({tool}, {endpoint})")
        if entry["revoked"]:
            raise DispenseDenied(f"credential for ({tool}, {endpoint}) is revoked")
        if node_id not in entry["scope_nodes"]:
            raise DispenseDenied(
                f"node {node_id!r} is not in the dispense scope for "
                f"({tool}, {endpoint}) — scope mismatch"
            )
        return {"secret": entry["secret"], "version": entry["version"]}

    async def rotate(self, tool: str, endpoint: str, new_secret: str) -> dict:
        data = await self._load()
        entry = data.get(_key(tool, endpoint))
        if entry is None:
            raise DispenseDenied(f"nothing enrolled for ({tool}, {endpoint})")
        entry["secret"] = new_secret
        entry["version"] += 1
        entry["revoked"] = False
        await self._save(data)
        return {"version": entry["version"]}

    async def revoke(self, tool: str, endpoint: str) -> dict:
        """Narrowing only: the plane may revoke in an incident; granting goes
        through enrollment config, never through a plane command."""
        data = await self._load()
        entry = data.get(_key(tool, endpoint))
        if entry is None:
            raise DispenseDenied(f"nothing enrolled for ({tool}, {endpoint})")
        entry["revoked"] = True
        await self._save(data)
        return {"revoked": True, "version": entry["version"]}

    async def health(self) -> dict:
        data = await self._load()
        return {
            "enrolled": [
                {"tool": e["tool"], "endpoint": e["endpoint"],
                 "version": e["version"], "revoked": e["revoked"],
                 "scope_nodes": e["scope_nodes"]}
                for e in data.values()
            ],
        }
