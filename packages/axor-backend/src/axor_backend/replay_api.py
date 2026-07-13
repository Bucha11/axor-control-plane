"""Replay over stored traces — imports the kernel fold, never reimplements it.

Three consumers (spec section 13): the scrubber (state per step), single-trace
counterfactuals (config edit -> first divergence), and the regression corpus
report ("CI for governance configs", decision 11).
"""
from __future__ import annotations

from typing import Any

from axor_core.contracts.canonical import ConsequenceClass
from axor_core.kernel.events import Event, EventKind, Verdict, event_from_json_line
from axor_core.kernel.replay import KernelConfig, ReplayResult, replay
from axor_core.policy.value_policy import ValuePredicate


def parse_trace(lines: list[str]) -> list[Event]:
    return [event_from_json_line(line) for line in lines]


def containment_report(
    events: list[Event], subgraph: dict[str, Any]
) -> dict[str, Any]:
    """Containment + systemic outcome (spec v2 Ch.2), grounded in the case.

    Pure-A by construction (v2-8): `held` counts discrete boundary DENIALS —
    verifiable events; `reached` adds consequences that PASSED while carrying
    the case's taint (escapes). Intra edges that carried taint are listed as
    informational rows ("carried, not laundered") — they are not enforcement
    boundaries (labels are data inside one federation, Ch.1 §1), so they never
    enter the ratio. The ungoverned twin is the same recorded trace with
    denials counterfactually ignored — deterministic, no second run (Ch.2 §3).

    Systemic outcome is a LABEL, never a number (v2-7).
    """
    case_nodes = {n["node_id"] for n in subgraph["nodes"]}
    case_refs: set[str] = set()
    for e in events:
        if e.node_id in case_nodes and e.payload.get("value_ref"):
            case_refs.add(str(e.payload.get("value_ref")))

    rows: list[dict[str, Any]] = []
    held = 0
    escaped = 0

    # Intra hops that carried the taint — informational, not counted.
    for edge in subgraph["edges"]:
        carried_root = (edge.get("carried") or {}).get("root") or {}
        if carried_root.get("sources"):
            rows.append({
                "edge": f'{edge["from"]} → {edge["to"]}',
                "note": "carried, not laundered",
                "status": "carried",
            })

    # Gated consequences: exports / peer sends driven by case taint.
    for e in events:
        if e.node_id not in case_nodes or e.verdict is None:
            continue
        driving = {str(r) for r in (e.payload.get("arg_refs") or {}).values()}
        if e.kind is EventKind.MESSAGE_SENT:
            ref = e.payload.get("value_ref")
            if ref:
                driving.add(str(ref))
            if e.payload.get("edge_kind") != "peer":
                continue  # intra sends are carried rows above
            target = f'{e.node_id} → {e.payload.get("to")}'
        elif e.kind is EventKind.TOOL_CALL:
            n = e.payload.get("normalized") or {}
            if n.get("destination_kind") not in ("external_domain", "workspace_share"):
                continue  # not an export consequence
            target = f'{e.node_id} → {e.payload.get("tool")}'
        else:
            continue
        if not (driving & case_refs):
            continue
        if e.verdict is Verdict.DENY:
            held += 1
            rows.append({"edge": target, "note": "DENIED — contained here",
                         "status": "held", "gate": e.gate})
        else:
            escaped += 1
            rows.append({"edge": target, "note": "ESCAPED", "status": "escaped"})

    reached = held + escaped
    fault = subgraph.get("fault_origin") is not None
    if not fault:
        governed_outcome = "honest_success"
    elif escaped:
        governed_outcome = "fabricated_failure"
    else:
        # denied a tainted consequence → an honest non-answer, not a success
        governed_outcome = "honest_failure" if held else "honest_success"
    # The twin: the same trace, denials ignored — every held boundary escapes.
    ungoverned_outcome = "fabricated_failure" if fault and reached else governed_outcome

    return {
        "rows": rows,
        "held": held,
        "reached": reached,
        "containment": f"{held}/{reached}" if reached else None,
        "governed_outcome": governed_outcome,
        "ungoverned_outcome": ungoverned_outcome,
    }


def influence_ranking(
    events: list[Event],
    config: KernelConfig,
    anchor_node: str,
    anchor_seq: int,
    refs: list[str],
) -> list[dict[str, Any]]:
    """Cross-node influence via subgraph ablation (spec v2 Ch.3 §7): replay
    the anchor node's local sequence with each upstream ref excised; a ref
    whose removal flips the anchor's verdict drove the discrepancy. Bounded by
    causal-chain length, deterministic, reuses the kernel fold (Rule 0)."""
    local = sorted(
        (e for e in events if e.node_id == anchor_node), key=lambda e: e.seq
    )

    def anchor_verdict(evs: list[Event]) -> str | None:
        result = replay(evs, config)
        for step in result.steps:
            if step.event.seq == anchor_seq:
                v = step.reevaluated_verdict or step.recorded_verdict
                return v.value if v else None
        return None

    baseline = anchor_verdict(local)
    ranked: list[dict[str, Any]] = []
    for ref in refs:
        excision = Event(
            seq=-1, node_id=anchor_node, kind=EventKind.CONTEXT_EXCISION,
            ts="ablation", payload={"refs": [ref], "reason": "ablation"},
        )
        ablated = anchor_verdict([excision, *local])
        ranked.append({
            "ref": ref,
            "influence": 1.0 if ablated != baseline else 0.0,
            "baseline_verdict": baseline,
            "ablated_verdict": ablated,
        })
    ranked.sort(key=lambda r: (-r["influence"], r["ref"]))
    return ranked


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
