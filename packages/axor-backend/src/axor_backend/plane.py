"""Control plane endpoints (protocol note v0.1).

GET  /v1/plane/{node_id}/desired    SSE: `snapshot` first, then deltas
POST /v1/plane/{node_id}/telemetry  batched kernel events, Idempotency-Key dedup
POST /v1/plane/{node_id}/command    declarative desired-state write, version++

Merge/absorb semantics live in axor_core.kernel.state.DesiredState — the backend
persists and fans out (Postgres LISTEN/NOTIFY -> SSE); it does not interpret.
Signature verification here is defense in depth only: the adapter re-verifies
with operator pubkeys from ITS OWN config — a compromised backend must not be
able to forge commands (protocol, section 6).
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/v1/plane")

# route stubs land with storage.py
