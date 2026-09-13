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
from axor_core.kernel.events import Event, EventKind, Verdict
from axor_eval.audit.retrieval_audit import RetrievalAuditLayer
from axor_eval.audit.tool_audit import ToolAuditLayer
from axor_eval.contracts import AgentClaims, EvidenceCase
from axor_eval.deprivation.engine import FaultRecord, ToolDeprivationEngine

from axor_proxy.recorder import TraceRecorder

# Fault modes the proxy applies WITHOUT calling upstream (the engine's wrapper
# never invokes the original callable for these).
_NO_UPSTREAM_MODES = frozenset({"silent_fail", "tool_substitution"})

# The kernel category a vault credential refusal is recorded under. The plane
# refused to hand this node a credential for this tool, which is the same thing
# the capability gate decides: this node may not make this call. The kernel's
# table (`axor_core.governor.GATE_OF_CATEGORY`) has no "vault" entry, and
# writing one into the `gate` column would put a name outside the vocabulary a
# recorded verdict may carry — the defect the demo trace was already fixed for.
# The specific reason survives in the payload; `gate` stays a kernel gate name.
VAULT_DENIAL_CATEGORY = "capability"


def build_governor(manifests: list[dict[str, Any]], node_id: str) -> object:
    """A ToolCallGovernor compiled from operator-supplied tool manifests.

    The proxy is an HTTP boundary: there is no callable to wrap, so
    `WrappedToolset` does not apply — but the governor underneath it does, and
    it is the same one. Manifests are the operator's, never inferred: an MCP
    `tools/list` names a tool and describes it, and nothing in that says whether
    it EXPORTS. Guessing the effect class is precisely what the config builder
    exists to stop, so a run is governed only when its manifests are handed in.
    """
    from axor_core.governor import ToolCallGovernor
    from axor_wrap.compile import governor_kwargs

    return ToolCallGovernor(**governor_kwargs(manifests), node_id=node_id)


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
    # Compiled from `manifests` when the run was armed with them; None means
    # this run is observed, not governed, and every recorded call says so.
    governor: object | None = None
    manifests: tuple[dict[str, Any], ...] = ()
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
    """Concurrent armed runs (launch-readiness §1): a shared proxy serves N
    runs at once. A caller names its run with the X-Axor-Run header; without
    the header the most recently armed run applies (single-user compat)."""

    def __init__(self, trace_dir: Path, retention_days: float | None = None) -> None:
        self._trace_dir = trace_dir
        self._runs: dict[str, Run] = {}
        # armed run_ids in arming order; last is the header-less default
        self._armed: list[str] = []
        self._retention_days = retention_days

    @property
    def active(self) -> Run | None:
        """The most recently armed run — the default when no header names one."""
        return self._runs[self._armed[-1]] if self._armed else None

    def active_for(self, run_id: str | None) -> Run | None:
        """Resolve the armed run a tool call belongs to. An explicit header
        naming an unknown/disarmed run resolves to None (503 at the route) —
        never silently falls back to somebody else's run."""
        if run_id is None:
            return self.active
        return self._runs[run_id] if run_id in self._armed else None

    def is_armed(self, run: Run) -> bool:
        return run.run_id in self._armed

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def prune_traces(self) -> int:
        """Retention: delete trace files older than the window (mtime). Called
        on each arm — cheap (one listdir). No-op when retention is unset."""
        if not self._retention_days or self._retention_days <= 0:
            return 0
        import time

        cutoff = time.time() - self._retention_days * 86400
        pruned = 0
        for f in self._trace_dir.glob("*.jsonl"):
            if f.stat().st_mtime < cutoff and f.stem not in self._armed:
                f.unlink(missing_ok=True)
                self._runs.pop(f.stem, None)
                pruned += 1
        return pruned

    def start(
        self, scenario: str, faults: list[dict[str, Any]], node_id: str = "proxy",
        manifests: list[dict[str, Any]] | None = None,
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
            governor=build_governor(manifests, node_id) if manifests else None,
            manifests=tuple(manifests or ()),
        )
        self._runs[run_id] = run
        self._armed.append(run_id)
        self.prune_traces()
        return run

    def disarm(self, run: Run | None = None) -> None:
        if run is None:
            self._armed.clear()
        elif run.run_id in self._armed:
            self._armed.remove(run.run_id)

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
        gate: str | None = None,
        verdict: Verdict | None = None,
    ) -> Event:
        """`gate` and `verdict` were not parameters, so nothing the proxy wrote
        could carry either — every event left both columns null and the trace
        was observation with no governance in it. A refused call recorded that
        way is not merely unlabelled: the replay fold re-gates the TOOL_CALL
        branch on `verdict`, so a null one reads as a call that HAPPENED and
        charges the budget for it."""
        event = Event(
            seq=run.next_seq(),
            node_id=run.node_id,
            kind=kind,
            ts=_now_iso(),
            causal_root=causal_root,
            gate=gate,
            verdict=verdict,
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
        self.disarm(run)
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
