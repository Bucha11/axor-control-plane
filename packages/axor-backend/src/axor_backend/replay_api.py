"""Replay over stored traces — imports the kernel fold, never reimplements it.

Three consumers (spec section 13): the scrubber (state per step), single-trace
counterfactuals (config edit -> first divergence), and the regression corpus
report ("CI for governance configs", decision 11).
"""
from __future__ import annotations

from typing import Any

from axor_core.contracts.canonical import ConsequenceClass
from axor_core.kernel.events import Event, EventKind, event_from_json_line
from axor_core.kernel.replay import KernelConfig, ReplayResult, replay
from axor_core.policy.value_policy import ValuePredicate


def parse_trace(lines: list[str]) -> list[Event]:
    return [event_from_json_line(line) for line in lines]


def kernel_config_from_json(d: dict[str, Any]) -> KernelConfig:
    """Accepts either the Config Builder shape ({"sinks": {...}}) or the
    direct kernel shape ({"allowed_tools": [...], ...})."""
    if "sinks" in d:
        sinks: dict[str, dict[str, Any]] = d["sinks"]
        egress = {n for n, s in sinks.items()
                  if s.get("consequence_class") == "EXPORT"}
        imperative = {n for n, s in sinks.items()
                      if s.get("consequence_class") == "EXEC"}
        value_policies: dict[str, list[ValuePredicate]] = {}
        driving_args: dict[str, frozenset[str]] = {}
        for name, sink in sinks.items():
            trusted = sink.get("trusted_sets") or {}
            if trusted:
                value_policies[name] = [
                    ValuePredicate(arg=arg, kind="enum", allowed=frozenset(vals))
                    for arg, vals in trusted.items()
                ]
                driving_args[name] = frozenset(trusted.keys())
        return KernelConfig(
            allowed_tools=frozenset(sinks.keys()),  # undeclared = denied
            egress_sinks=frozenset(egress),
            imperative_sinks=frozenset(imperative),
            value_policies=value_policies,
            driving_args=driving_args,
            budget_cap_calls=d.get("budget_cap_calls"),
            budget_cap_cost=d.get("budget_cap_cost"),
            tool_weights=dict(d.get("tool_weights", {})),
            synthetic_taint_refs=frozenset(d.get("synthetic_taint_refs", ())),
        )
    return KernelConfig(
        allowed_tools=(
            frozenset(d["allowed_tools"]) if d.get("allowed_tools") is not None else None
        ),
        egress_sinks=frozenset(d.get("egress_sinks", ())),
        imperative_sinks=frozenset(d.get("imperative_sinks", ())),
        positional_sinks=frozenset(d.get("positional_sinks", ())),
        value_policies={
            tool: [
                ValuePredicate(
                    arg=p["arg"], kind=p.get("kind", "enum"),
                    lo=p.get("lo"), hi=p.get("hi"),
                    allowed=frozenset(p.get("allowed", ())),
                )
                for p in preds
            ]
            for tool, preds in d.get("value_policies", {}).items()
        },
        driving_args={
            tool: frozenset(args) for tool, args in d.get("driving_args", {}).items()
        },
        consequence_overrides={
            tool: ConsequenceClass[name]
            for tool, name in d.get("consequence_overrides", {}).items()
        },
        budget_cap_calls=d.get("budget_cap_calls"),
        budget_cap_cost=d.get("budget_cap_cost"),
        tool_weights=dict(d.get("tool_weights", {})),
        synthetic_taint_refs=frozenset(d.get("synthetic_taint_refs", ())),
    )


def scrubber_payload(result: ReplayResult) -> dict[str, Any]:
    return {
        "first_divergence": result.first_divergence,
        "steps": [
            {
                "seq": s.event.seq,
                "kind": s.event.kind.value,
                "gate": s.event.gate,
                "recorded_verdict": s.recorded_verdict.value if s.recorded_verdict else None,
                "reevaluated_verdict": (
                    s.reevaluated_verdict.value if s.reevaluated_verdict else None
                ),
                "deny_reason": s.deny.reason if s.deny else None,
                "deny_category": s.deny.category if s.deny else None,
                "hypothetical": s.hypothetical,
                "payload": s.event.payload,
                "state": {
                    "level": s.state.level.name,
                    "tainted_refs": sorted(s.state.tainted_refs),
                    "excised_refs": sorted(s.state.excised_refs),
                    "floor_active": s.state.floor_active,
                    "budget_spent_calls": s.state.budget_spent_calls,
                    "budget_spent_cost": s.state.budget_spent_cost,
                    "facts": len(s.state.facts),
                },
            }
            for s in result.steps
        ],
    }


def _recorded_denies(events: list[Event]) -> bool:
    return any(
        e.verdict is not None and e.verdict.value == "deny"
        for e in events
        if e.kind is EventKind.TOOL_CALL
    )


def _reevaluated_denies(result: ReplayResult) -> bool:
    return any(
        s.reevaluated_verdict is not None and s.reevaluated_verdict.value == "deny"
        for s in result.steps
    )


def regression_row(
    run_id: str, side: str, label: str, events: list[Event], config: KernelConfig
) -> dict[str, Any]:
    """One corpus trace vs a candidate config (regression-report semantics):

    must_block + still denies somewhere -> held; no denial left -> escaped.
    must_pass + no new denial -> passed; new denial -> regressed.
    """
    result = replay(events, config)
    denies_now = _reevaluated_denies(result)
    if side == "must_block":
        outcome = "held" if denies_now else "escaped"
    else:
        outcome = "regressed" if result.first_divergence is not None and denies_now else "passed"
    first_new_deny = next(
        (
            {"seq": s.event.seq, "reason": s.deny.reason, "category": s.deny.category}
            for s in result.steps
            if s.deny is not None and s.recorded_verdict is not None
            and s.reevaluated_verdict is not s.recorded_verdict
        ),
        None,
    )
    return {
        "run_id": run_id,
        "side": side,
        "label": label,
        "result": outcome,
        "first_divergence": result.first_divergence,
        "new_denial": first_new_deny,
    }
