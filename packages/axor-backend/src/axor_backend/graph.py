"""GraphStore: Sentinel taint/reputation graph behind a Protocol.

Kuzu everywhere (architecture decision): embedded, per-tenant DB file on
hosted (tenant isolation for free), same Cypher-ish queries on both deploys.
Neo4j remains a legacy hosted backend behind this same Protocol until migrated.
"""
from __future__ import annotations

from typing import Protocol


class GraphStore(Protocol):
    # object: nodes+edges payload for UI
    async def khop(self, focus: str, k: int, limit: int) -> dict[str, object]: ...
    async def append_attestation(self, fact_json: str) -> None: ...
    async def hottest_branches(self, node_id: str, limit: int) -> list[str]: ...


class KuzuGraphStore:
    """One writer per tenant DB file — confirm against ingest path (arch open q)."""

    def __init__(self, db_dir: str, tenant: str) -> None:
        self._db_path = f"{db_dir}/{tenant}.kuzu"

    async def khop(self, focus: str, k: int, limit: int) -> dict[str, object]:
        raise NotImplementedError
