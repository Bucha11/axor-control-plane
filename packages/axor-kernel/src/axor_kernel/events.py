"""Versioned event schema — the trace contract (architecture doc, section 1).

One event per JSONL line. Single source of truth for runtime, storage,
telemetry, and replay. Frontend TS types are generated from these models
(JSON Schema -> ts), never hand-maintained.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0"


class EventKind(StrEnum):
    INTENT = "intent"
    GATE_EVAL = "gate_eval"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    FAULT_INJECTED = "fault_injected"
    CLAIM = "claim"
    DENIAL = "denial"
    FACT = "fact"  # feeds degradation (incl. operator_attestation)
    OPERATOR_INTERVENTION = "operator_intervention"  # marks the run `intervened`
    STATE_APPLIED = "state_applied"                  # desired-state version ack
    INJECTION_CONSUMED = "injection_consumed"
    HEARTBEAT = "heartbeat"


class Verdict(StrEnum):
    PASS = "pass"
    DENY = "deny"


class Event(BaseModel):
    """A single trace line. Append-only; never mutated after emission."""

    schema_version: str = SCHEMA_VERSION
    seq: int = Field(ge=0, description="Monotonic per node")
    node_id: str
    kind: EventKind
    ts: str = Field(description="ISO 8601; informational — ordering is by seq")
    causal_root: str | None = None
    gate: str | None = None
    verdict: Verdict | None = None
    # object: kind-specific, validated downstream
    payload: dict[str, object] = Field(default_factory=dict)


class Fact(BaseModel):
    """Degradation-machine input. Attestations are facts too (spec 8.1.1)."""

    fact_id: str
    fact_type: str
    severity: int = Field(ge=0, description="Feeds compute_level; 0 = informational")
    covers: tuple[str, ...] = ()   # for attestations: fact_ids covered
    revokes: str | None = None     # for attestation revocation: attestation fact_id
    operator: str | None = None
    reason: str | None = None
    sig: str | None = None         # ed25519, required for operator-originated facts
