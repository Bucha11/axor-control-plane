"""Replay over stored traces — imports the kernel fold, never reimplements it.

Three consumers (spec section 13): the scrubber (state per step), single-trace
counterfactuals (config edit -> first divergence), and the regression corpus
report ("CI for governance configs", decision 11).
"""
from __future__ import annotations

from typing import Any

from axor_core.contracts.canonical import ConsequenceClass
from axor_core.kernel.events import Event, EventKind, Verdict, event_from_json_line
from axor_core.kernel.replay import KernelConfig, ReplayResult, ReplayStep, replay
from axor_core.policy.value_policy import ValuePredicate

from axor_backend.errors import ConfigInvalid
from axor_backend.wrap_api import EFFECT_CLASSES


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


def _kind(value: object) -> str:
    return "null" if value is None else type(value).__name__


def _obj(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigInvalid(f"{field} must be an object, got {_kind(value)}")
    return value


def _seq(value: object, field: str) -> list[Any]:
    """A JSON list, refusing the bare string that looks like one.

    ``frozenset("slack_post")`` is a set of nine single characters, so every
    membership test the kernel runs against it answers False and the config
    silently means the opposite of what was written — while the request still
    answers 200 with a confident report. It is the one config typo that
    produces a working call and a wrong policy, so it is refused, not coerced.
    """
    if isinstance(value, (str, bytes, dict)) or not isinstance(
        value, (list, tuple, set, frozenset)
    ):
        hint = f" — wrap {value!r} in a list" if isinstance(value, str) else ""
        raise ConfigInvalid(f"{field} must be a list, got {_kind(value)}{hint}")
    return list(value)


def _names(value: object, field: str) -> frozenset[str]:
    items = _seq(value, field)
    for item in items:
        if not isinstance(item, str):
            raise ConfigInvalid(
                f"{field}: every entry must be a name, got {_kind(item)}"
            )
    return frozenset(items)


def _value_set(value: object, field: str) -> frozenset[Any]:
    """An admissible-value set: scalars, unlike :func:`_names`, since a trusted
    set may enumerate numbers as legitimately as it enumerates strings."""
    items = _seq(value, field)
    for item in items:
        if not isinstance(item, (str, int, float, bool)):
            raise ConfigInvalid(
                f"{field}: entries must be scalars, got {_kind(item)}"
            )
    return frozenset(items)


def _number(value: object, field: str) -> int | float | None:
    """A number or None. A cap the kernel compares with ``>=`` against a string
    raises deep inside the fold, which surfaces as a 500 on a config typo."""
    if value is None or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return value
    raise ConfigInvalid(f"{field} must be a number, got {_kind(value)}")


def _weights(value: object) -> dict[str, float]:
    weights = _obj(value, "tool_weights")
    out: dict[str, float] = {}
    for tool, weight in weights.items():
        if _number(weight, f"tool_weights[{tool!r}]") is None:
            raise ConfigInvalid(f"tool_weights[{tool!r}] must be a number, got null")
        out[str(tool)] = weight
    return out


def _consequence_overrides(value: object) -> dict[str, ConsequenceClass]:
    overrides = _obj(value, "consequence_overrides")
    out: dict[str, ConsequenceClass] = {}
    for tool, name in overrides.items():
        if not isinstance(name, str) or name not in ConsequenceClass.__members__:
            raise ConfigInvalid(
                f"consequence_overrides[{tool!r}]: {name!r} is not a consequence "
                f"class — one of {'|'.join(ConsequenceClass.__members__)}"
            )
        out[str(tool)] = ConsequenceClass[name]
    return out


def _predicates(tool: str, preds: object) -> list[ValuePredicate]:
    field = f"value_policies[{tool!r}]"
    out: list[ValuePredicate] = []
    for i, raw in enumerate(_seq(preds, field)):
        at = f"{field}[{i}]"
        pred = _obj(raw, at)
        arg = pred.get("arg")
        if not isinstance(arg, str) or not arg:
            raise ConfigInvalid(
                f"{at}: 'arg' is required — it names the argument the predicate "
                f"constrains"
            )
        out.append(ValuePredicate(
            arg=arg,
            kind=str(pred.get("kind", "enum")),
            lo=_number(pred.get("lo"), f"{at}.lo"),
            hi=_number(pred.get("hi"), f"{at}.hi"),
            allowed=_value_set(pred.get("allowed", ()), f"{at}.allowed"),
        ))
    return out


def kernel_config_from_json(d: dict[str, Any]) -> KernelConfig:
    """Accepts either the Config Builder shape ({"sinks": {...}}) or the
    direct kernel shape ({"allowed_tools": [...], ...}).

    Validated here rather than at the first gate that trips over it. All three
    consumers (scrubber, counterfactual, regression corpus) answer 200 with a
    confident-looking verdict computed from whatever survived a silent
    coercion, so a malformed config is a 400 naming the field — never a 500,
    and never a report.

    The keys both shapes share are built once, in ``common``: the two branches
    had drifted, and the ``sinks`` branch dropped ``positional_sinks`` and
    ``consequence_overrides`` on the floor.
    """
    d = _obj(d, "config")
    common: dict[str, Any] = {
        "positional_sinks": _names(
            d.get("positional_sinks", ()), "positional_sinks"
        ),
        "consequence_overrides": _consequence_overrides(
            d.get("consequence_overrides", {})
        ),
        "budget_cap_calls": _number(d.get("budget_cap_calls"), "budget_cap_calls"),
        "budget_cap_cost": _number(d.get("budget_cap_cost"), "budget_cap_cost"),
        "tool_weights": _weights(d.get("tool_weights", {})),
        "synthetic_taint_refs": _names(
            d.get("synthetic_taint_refs", ()), "synthetic_taint_refs"
        ),
    }
    if "sinks" in d:
        sinks = _obj(d["sinks"], "sinks")
        egress: set[str] = set()
        imperative: set[str] = set()
        value_policies: dict[str, list[ValuePredicate]] = {}
        driving_args: dict[str, frozenset[str]] = {}
        for name, raw in sinks.items():
            sink = _obj(raw, f"sinks[{name!r}]")
            effect = sink.get("consequence_class")
            # A misspelled class ("export", "?") used to make the sink neither
            # egress nor imperative: a config that reads as governed, gates
            # nothing, and reports safe to ship.
            if effect not in EFFECT_CLASSES:
                raise ConfigInvalid(
                    f"sinks[{name!r}].consequence_class must be one of "
                    f"{'|'.join(EFFECT_CLASSES)}, got {effect!r}"
                )
            if effect == "EXPORT":
                egress.add(name)
            elif effect == "EXEC":
                imperative.add(name)
            trusted = _obj(
                sink.get("trusted_sets") or {}, f"sinks[{name!r}].trusted_sets"
            )
            if trusted:
                value_policies[name] = [
                    ValuePredicate(
                        arg=arg, kind="enum",
                        allowed=_value_set(
                            vals, f"sinks[{name!r}].trusted_sets[{arg!r}]"
                        ),
                    )
                    for arg, vals in trusted.items()
                ]
                driving_args[name] = frozenset(trusted.keys())
        return KernelConfig(
            allowed_tools=frozenset(sinks.keys()),  # undeclared = denied
            egress_sinks=frozenset(egress),
            imperative_sinks=frozenset(imperative),
            value_policies=value_policies,
            driving_args=driving_args,
            **common,
        )
    return KernelConfig(
        allowed_tools=(
            _names(d["allowed_tools"], "allowed_tools")
            if d.get("allowed_tools") is not None else None
        ),
        egress_sinks=_names(d.get("egress_sinks", ()), "egress_sinks"),
        imperative_sinks=_names(d.get("imperative_sinks", ()), "imperative_sinks"),
        value_policies={
            tool: _predicates(tool, preds)
            for tool, preds in _obj(
                d.get("value_policies", {}), "value_policies"
            ).items()
        },
        driving_args={
            tool: _names(args, f"driving_args[{tool!r}]")
            for tool, args in _obj(d.get("driving_args", {}), "driving_args").items()
        },
        **common,
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


def _pinned_denials(result: ReplayResult) -> list[ReplayStep]:
    """The calls this trace recorded as DENY — the boundary a must_block pin
    exists to hold. Named steps, because "some step denies" is not the same
    claim as "the step that stopped the attack still stops it"."""
    return [
        s for s in result.steps
        if s.event.kind is EventKind.TOOL_CALL
        and s.recorded_verdict is Verdict.DENY
    ]


def _step_ref(step: ReplayStep) -> dict[str, Any]:
    return {
        "node_id": step.event.node_id,
        "seq": step.event.seq,
        "tool": str(step.event.payload.get("tool", "")),
        "gate": step.event.gate,
    }


def regression_row(
    run_id: str, side: str, label: str, events: list[Event], config: KernelConfig
) -> dict[str, Any]:
    """One corpus trace vs a candidate config (regression-report semantics).

    must_block holds only if EVERY call the trace recorded as DENY still
    denies. The old check asked whether the config denies *anything* in the
    trace, which a config that allows the exfil and denies an unrelated benign
    call satisfies — the corpus then answered ``held``, and ``safe_to_ship``,
    for a config that ships the breach.

    A must_block pin whose trace recorded no denial at all has nothing to hold:
    it was recorded ungoverned, so the call that should be blocked is not
    marked anywhere in it. That is ``unanchored`` — reported, and never counted
    as safe to ship, because the alternative is to guess which denial was meant
    and then call the guess a verification.

    must_pass mirrors it: a regression is a call the trace recorded as PASS
    that the candidate config now denies.

    Steps after the first divergence are judged too. Under a config that denies
    something earlier, the recorded tail is counterfactual and an escape there
    might never be reached — but "the gate that held this value no longer holds
    it" is the finding, and excusing it because an unrelated earlier call was
    blocked is the same fail-open in a politer form.
    """
    result = replay(events, config)
    pinned = _pinned_denials(result)
    escaped_denials = [
        _step_ref(s) for s in pinned if s.reevaluated_verdict is not Verdict.DENY
    ]
    new_denials = [
        s for s in result.steps
        if s.event.kind is EventKind.TOOL_CALL
        and s.recorded_verdict is Verdict.PASS
        and s.reevaluated_verdict is Verdict.DENY
    ]
    if side == "must_block":
        if not pinned:
            outcome = "unanchored"
        else:
            outcome = "escaped" if escaped_denials else "held"
    else:
        outcome = "regressed" if new_denials else "passed"
    first_new_deny = next(
        (
            {"seq": s.event.seq, "reason": s.deny.reason, "category": s.deny.category}
            for s in new_denials if s.deny is not None
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
        "pinned_denials": len(pinned),
        "escaped_denials": escaped_denials,
    }
