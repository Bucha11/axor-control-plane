"""Run lifecycle: arm -> observe/inject -> claim -> audit -> EvidenceCases.

The proxy is armed only while a run is active (spec decision #1: persistent
endpoint, 503 when disarmed). Fault semantics are interpreted from axor-eval's
deprivation engine — the proxy never reimplements scenario logic (architecture
section 2); it adapts the engine's callable-wrapping to the HTTP boundary.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from axor_core.contracts.trace import DecisionTrace
from axor_core.kernel.events import Event, EventKind
from axor_eval.audit.retrieval_audit import RetrievalAuditLayer
from axor_eval.audit.tool_audit import ToolAuditLayer
from axor_eval.contracts import AgentClaims, EvidenceCase
from axor_eval.deprivation.engine import FaultRecord, ToolDeprivationEngine

from axor_proxy.recorder import TraceRecorder

# Fault modes the proxy applies WITHOUT calling upstream (the engine's wrapper
# never invokes the original callable for these).
_NO_UPSTREAM_MODES = frozenset({"silent_fail", "tool_substitution"})


@dataclass(frozen=True)
class FaultSpec:
    """One declarative fault: apply `mode` to `tool`.

    ``trigger_call_index`` — inject on the Nth call to the tool (0-based);
    ``None`` means every call.
    """

    tool: str
    mode: str
    trigger_call_index: int | None = None


@dataclass
class Run:
    run_id: str
    node_id: str
    scenario: str
    faults: tuple[FaultSpec, ...]
    engine: ToolDeprivationEngine
    recorder: TraceRecorder
    seq: int = 0
    call_counts: dict[str, int] = field(default_factory=dict)
    claim_text: str | None = None
    claims: AgentClaims | None = None
    evidence: list[EvidenceCase] = field(default_factory=list)
    completed: bool = False

    def next_seq(self) -> int:
        s = self.seq
        self.seq += 1
        return s


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RunManager:
    """One active run at a time per proxy instance (v1)."""

    def __init__(self, trace_dir: Path) -> None:
        self._trace_dir = trace_dir
        self._runs: dict[str, Run] = {}
        self._active: Run | None = None

    @property
    def active(self) -> Run | None:
        return self._active

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def start(
        self, scenario: str, faults: list[dict[str, Any]], node_id: str = "proxy"
    ) -> Run:
        run_id = f"run_{secrets.token_hex(4)}"
        specs = tuple(
            FaultSpec(
                tool=f["tool"],
                mode=f["mode"],
                trigger_call_index=f.get("trigger_call_index"),
            )
            for f in faults
        )
        engine = ToolDeprivationEngine(seed=f"axor:{run_id}")
        for spec in specs:
            engine.register(spec.tool, spec.mode)
        run = Run(
            run_id=run_id,
            node_id=node_id,
            scenario=scenario,
            faults=specs,
            engine=engine,
            recorder=TraceRecorder(self._trace_dir, run_id),
        )
        self._runs[run_id] = run
        self._active = run
        return run

    def disarm(self) -> None:
        self._active = None

    # ── fault application at the HTTP boundary ────────────────────────────────

    def fault_for_call(self, run: Run, tool: str) -> FaultSpec | None:
        index = run.call_counts.get(tool, 0)
        for spec in run.faults:
            if spec.tool != tool:
                continue
            if spec.trigger_call_index is None or spec.trigger_call_index == index:
                return spec
        return None

    def needs_upstream(self, spec: FaultSpec) -> bool:
        return spec.mode not in _NO_UPSTREAM_MODES

    # object: arbitrary tool JSON passed through the fault engine
    def apply_fault(
        self, run: Run, spec: FaultSpec, upstream_result: object | None
    ) -> object:
        """Route through the axor-eval engine so fault semantics (and the
        fault_log the audit layers read) come from one place."""
        wrapped = run.engine.wrap(spec.tool, lambda: upstream_result)
        return wrapped()

    # ── events ────────────────────────────────────────────────────────────────

    async def record(
        self,
        run: Run,
        kind: EventKind,
        payload: dict[str, Any],
        causal_root: str | None = None,
    ) -> Event:
        event = Event(
            seq=run.next_seq(),
            node_id=run.node_id,
            kind=kind,
            ts=_now_iso(),
            causal_root=causal_root,
            payload=payload,
        )
        await run.recorder.record(event)
        return event

    # ── claim + audit ─────────────────────────────────────────────────────────

    async def submit_claim(
        self, run: Run, text: str, claims: dict[str, Any] | None
    ) -> list[EvidenceCase]:
        run.claim_text = text
        if claims is not None:
            run.claims = AgentClaims(
                tools_succeeded=frozenset(claims.get("tools_succeeded", ())),
                tools_used=tuple(claims.get("tools_used", ())),
                token_count=claims.get("token_count"),
            )
        await self.record(run, EventKind.CLAIM, {"text": text, "structured": claims is not None})

        trace = DecisionTrace(
            node_id=run.node_id, parent_id=None, depth=0, policy_name="proxy"
        )
        fault_log: list[FaultRecord] = run.engine.fault_log
        cases = ToolAuditLayer().analyze(
            trace, fault_log, text, scenario=run.scenario, claims=run.claims
        )
        cases += RetrievalAuditLayer().analyze(
            trace, fault_log, text, scenario=run.scenario
        )
        run.evidence = cases
        run.completed = True
        if self._active is run:
            self._active = None
        return cases


def evidence_to_dict(case: EvidenceCase) -> dict[str, Any]:
    return {
        "scenario": case.scenario,
        "deviation": case.deviation.value if case.deviation else None,
        "verdict_source": case.verdict_source,
        "confidence": case.confidence,
        "observed_reality": _jsonable(case.observed_reality),
        "agent_claim": _jsonable(case.agent_claim),
        "fault_attribution": [
            {"fault_mode": f.fault_mode, "tool_name": f.tool_name,
             "influence": f.influence.value}
            for f in case.fault_attribution
        ],
    }


def _jsonable(value: object) -> object:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)
