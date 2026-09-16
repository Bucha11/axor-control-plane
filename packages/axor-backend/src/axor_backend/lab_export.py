"""CP → Axor Lab: convert a recorded run into an incident package.

The package (`axor-lab-incident/v1`) is the second-funnel input of the Lab
(`axor-lab import-incident`): the frozen trace plus the scenario / tool
manifests / recorded condition it needs to validate, REPLAY and pin the
incident.  Everything here is synthesized from what the Control Plane actually
recorded — adapter-depth events with explicit per-value provenance (arg_refs →
value_ref, roots) and recorded gate verdicts.  A run that lacks any of that
(proxy-depth observations, message-gate denials, verdicts the label-based
replay cannot reproduce) is refused with the full list of reasons, never
exported as something the Lab would misreproduce.

Replay-backend honesty: the exported condition pins Lab's
``reference_taint_floor_kernel``.  CP traces carry *explicit-flow labels*
(taint roots per value ref), which is exactly what that reference decide
replays over; Lab's real-kernel backend (``ToolCallGovernor``) is
content-derivation based and needs concrete payload bodies the Control Plane
deliberately does not record (observations only, no raw bodies).  The
axor-core build that actually produced the verdicts is recorded where the
contracts put shared kernel identity: ``condition.kernel_ref`` and
``trace.producer.runtime``.  Before exporting, the converter recomputes every
decision under the reference semantics and refuses the run if any recorded
verdict would not reproduce — so a package that leaves this endpoint imports
with ``replay: match``, or it does not leave at all.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from axor_core.policy.from_record import (
    IncompleteRecord,
    causal_root_from_record,
    normalized_from_record,
)
from axor_core.policy.gates import taint_gate

from axor_backend.errors import BackendError
from axor_backend.signing import jcs_canonical

INCIDENT_SCHEMA = "axor-lab-incident/v1"

# Lab's reference decide (lab_runner.kernel) — the label-based replay backend
# the exported condition pins.  The gate name is the one that kernel emits for
# every decision; the recorded axor-core build goes into kernel_ref / runtime.
REFERENCE_KERNEL = "reference_taint_floor_kernel"
GATE_TAINT_FLOOR = "taint_floor"
PROJECTION_UNTRUSTED = "untrusted-derived"
# reference-kernel-executable policy (profiles/trust models it actually runs)
LAB_POLICY: dict[str, Any] = {"profile": "default", "trust_model": "content-ledger"}

LABEL_UNTRUSTED = "untrusted_derived"
LABEL_TRUSTED = "trusted"
LABEL_SENSITIVE = "sensitive"

# tool_call destinations the kernel treats as an export consequence
# (replay_api.containment_report) — the only recorded signal of egress-ness.
_EGRESS_DESTINATIONS = frozenset({"external_domain", "workspace_share"})
_EGRESS_CLASSES = frozenset({"EXPORT", "EXEC"})
# the single coarse result field the synthesized manifests declare untrusted —
# CP records field-level provenance as roots on the whole value ref.
_RESULT_FIELD = "content"

_PREVIEW_LEN = 80

class LabExportError(BackendError):
    """The run cannot be converted to a Lab incident package; ``reasons`` lists
    every missing/unrepresentable thing, not just the first."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__(
            "run is not convertible to an axor-lab incident package: "
            + "; ".join(reasons)
        )
        self.reasons: tuple[str, ...] = tuple(reasons)


def content_hash(obj: object) -> str:
    """``sha256:<hex>`` over the RFC 8785 canonical serialization — byte-identical
    to ``lab_contracts.canonical.content_hash`` on the float-free values exported
    here (parity pinned by the JCS vector suite both repos share)."""
    return "sha256:" + hashlib.sha256(jcs_canonical(obj)).hexdigest()


def condition_config_hash(kernel: str, policy: dict[str, Any] | None) -> str:
    """Mirror of ``lab_contracts.condition_config_hash`` (same canonicalization)."""
    return content_hash({"kernel": kernel, "policy": policy or {}})


def world_digest(inputs: dict[str, Any], fixtures: dict[str, Any]) -> str:
    """Mirror of ``lab_contracts.canonical.world_digest`` — the trace's
    ``inputs_digest`` must bind the scenario's inputs + fixtures byte-identically."""
    return content_hash({"inputs": inputs, "fixtures": fixtures})


# ── intermediate views over the recorded run ─────────────────────────────────


@dataclass
class _Call:
    node: str
    seq: int
    tool: str
    args: dict[str, Any]
    arg_refs: dict[str, str]
    verdict: str
    egress: bool
    # the driving args the producing kernel DECLARED for this sink, when it
    # recorded them; empty for a proxy-depth trace that carries no declaration.
    declared_driving: list[str] = field(default_factory=list)
    # What the kernel decided ON, exactly as it recorded it: the structural
    # projection, the driving value's causal root, and whether the
    # confidentiality floor was up. `axor_core.policy.gates.taint_gate` takes
    # these three and nothing else, so recomputing a verdict needs no content —
    # and needs no reimplementation of the gate either.
    normalized: dict[str, Any] = field(default_factory=dict)
    driving_root: dict[str, Any] | None = None
    floor_active: bool = False
    decision: dict[str, Any] | None = None


@dataclass
class _Value:
    value_id: str
    tool: str
    sources: list[str]
    sensitive: bool
    derived_from: list[str] = field(default_factory=list)
    decision_value: Any = None
    bound: bool = False
    # True only for a value the MODEL composed from its context. `trace/v1`'s
    # `transformations` enum reserves `model_extraction` for exactly that, and a
    # tool-derived value must not claim it — a summarizer output is not an LLM
    # extraction, and the constructor set is closed on purpose.
    model_composed: bool = False

    @property
    def untrusted(self) -> bool:
        return bool(self.sources)

    def labels(self) -> list[str]:
        labels = [LABEL_UNTRUSTED if self.untrusted else LABEL_TRUSTED]
        if self.sensitive:
            labels.append(LABEL_SENSITIVE)
        return labels


@dataclass
class _Tool:
    tool: str
    arg_types: dict[str, str] = field(default_factory=dict)
    egress: bool = False
    driving_args: set[str] = field(default_factory=set)
    untrusted_source: bool = False
    sensitive_source: bool = False
    has_result: bool = False


def _json_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    if value is None:
        return "null"
    return "string"


# ── the converter ────────────────────────────────────────────────────────────


def build_incident_package(
    run_events: list[dict[str, Any]],
    run_meta: dict[str, Any],
    *,
    source_url: str | None = None,
) -> dict[str, Any]:
    """Convert one recorded run into an ``axor-lab-incident/v1`` package.

    Raises :class:`LabExportError` with the complete reason list when the run
    is not convertible (proxy-depth events, unrepresentable event kinds,
    verdicts the label-based replay would not reproduce, no injection vector,
    no sink)."""
    run_id = str(run_meta.get("run_id", ""))
    scenario_id = str(run_meta.get("scenario") or "cp-run")
    reasons: list[str] = []

    kernel_events = [e for e in run_events if e.get("schema_version")]
    if not kernel_events:
        raise LabExportError(
            ["run has no kernel-schema events to convert (plane telemetry only "
             "— e.g. heartbeats); Lab replay needs the adapter trace depth"]
        )

    calls, values, out_events = _scan(kernel_events, reasons)
    tools = _tool_table(calls, values)

    if not any(t.untrusted_source for t in tools.values()):
        reasons.append(
            "no untrusted source in the recorded run (no tool_result carries a "
            "tainted root) — a Lab scenario needs an injection vector"
        )
    # The scenario's violation predicate reads `prov(args.<driving>)`, so an
    # egress sink is only usable if the recording says which argument drives it.
    # Testing only for `egress` here and the stronger condition later meant a run
    # was refused for "no egress consequence", the operator added a sink, and it
    # was refused again — by a DIFFERENT message, raised alone, after the reason
    # list had already been reported. Every reason, in one list, is this module's
    # whole contract with its caller.
    usable_sinks = [t for t in tools.values() if t.egress and t.driving_args]
    if not any(t.egress for t in tools.values()):
        reasons.append(
            "no recorded egress consequence (tool_call with "
            "normalized.destination_kind external_domain/workspace_share, or a "
            "tool the operator declared an egress sink) — a Lab scenario needs a "
            "WRITE/EXPORT/EXEC sink to breach"
        )
    elif not usable_sinks:
        reasons.append(
            "every recorded egress sink is missing its driving argument "
            "(`driving_args` on the tool_call payload) — the Lab scenario's "
            "violation predicate is written over `prov(args.<driving>)` and "
            "cannot be expressed without one"
        )

    manifests = [_manifest(t) for t in tools.values()]
    condition = _condition(run_id)
    _decide_all(calls, tools, values, reasons)

    if reasons:
        raise LabExportError(reasons)

    scenario = _scenario(scenario_id, run_id, run_meta, tools, calls)
    trace = _trace(run_id, scenario_id, condition, scenario, calls, values, out_events)

    return {
        "schema_version": INCIDENT_SCHEMA,
        "trace": trace,
        "scenario": scenario,
        "manifests": manifests,
        "condition": condition,
        "replay_fidelity": _replay_fidelity(),
        "source": {
            "product": "control-plane",
            "run_id": run_id,
            "url": source_url or f"/v1/runs/{run_id}",
        },
    }


def _model_values(
    node: str,
    seq: int,
    declared: list[str],
    arg_refs: dict[str, str],
    args: dict[str, Any],
    payload: dict[str, Any],
    values: dict[str, _Value],
) -> dict[str, str]:
    """Ledger entries for driving arguments the MODEL produced, not a tool.

    The label-based replay resolves a driving argument through its value ref and
    fails closed when there is none. That is right for a proxy-depth trace: the
    proxy mints a ref for every value it sees, so a missing one really does mean
    "we never observed where this came from".

    It is wrong for a content-derivation kernel (`axor_core.ToolCallGovernor`,
    and the IntentLoop). That kernel mints a ref only for a value some tool
    RETURNED; an argument the model composed from its own context — a recipient
    the user named, a constant — has no ref and never will. Failing closed there
    turned every recorded ALLOW on a clean egress into a recomputed DENY, and the
    converter refused the whole run for a verdict mismatch it had manufactured.

    The kernel does record what it derived for that argument: ``arg_provenance``
    states, per argument, the sources it carries and whether it is sensitive, and
    an empty ``sources`` list means CLEAN — the kernel derived it and found
    nothing — not "unrecorded". So a value is minted from that statement. An
    argument with no ref AND no recorded provenance still gets nothing, and the
    fail-closed path below still fires: this uses evidence, it does not invent
    any.
    """
    provenance: dict[str, Any] = payload.get("arg_provenance") or {}
    # The untrusted values live in the model's context at this call. trace/v1 is
    # explicit that a model_extraction's `derived_from` is ALL of them, not a
    # guessed subset: nobody proved which one the model copied, and the
    # conservative join is what the soundness argument rests on
    # (contracts/provenance-semantics.md §2). Without these edges a composed
    # value had no traceable parent at all, and the ledger ended up naming a
    # source that does not exist.
    live_untrusted = sorted(v.value_id for v in values.values() if v.untrusted)
    minted: dict[str, str] = {}
    for arg in declared:
        if arg in arg_refs or arg not in provenance:
            continue
        recorded = provenance[arg] or {}
        value_id = f"m_{node}_{seq}_{arg}"
        sources = [str(x) for x in (recorded.get("sources") or [])]
        values[value_id] = _Value(
            value_id=value_id,
            tool="",  # no producing tool — it did not come out of one
            sources=sources,
            sensitive=bool(recorded.get("sensitive")),
            derived_from=list(live_untrusted) if sources else [],
            decision_value=args.get(arg),
            bound=True,
            model_composed=True,
        )
        minted[arg] = value_id
    return minted


# ── pass 1: recorded events → calls / values / trace-event plan ──────────────


def _scan(
    kernel_events: list[dict[str, Any]], reasons: list[str]
) -> tuple[list[_Call], dict[str, _Value], list[tuple[str, str, dict[str, Any]]]]:
    """One ordered pass over the recorded lines.

    Returns (calls, values by ref, planned trace events).  A planned event is
    (node, type, extra) — seq/call_id are assigned in :func:`_trace` once the
    full plan exists.  Anything without a faithful trace/v1 representation
    appends a reason instead of being silently dropped."""
    calls: list[_Call] = []
    values: dict[str, _Value] = {}
    plan: list[tuple[str, str, dict[str, Any]]] = []
    last_call: dict[str, _Call] = {}

    for event in kernel_events:
        kind = str(event.get("kind", ""))
        node = str(event.get("node_id") or "root")
        payload: dict[str, Any] = event.get("payload") or {}
        seq = int(event.get("seq", 0))
        if kind == "tool_call":
            tool = str(payload.get("tool", ""))
            verdict = event.get("verdict")
            arg_refs = {str(k): str(v) for k, v in (payload.get("arg_refs") or {}).items()}
            args: dict[str, Any] = payload.get("args") or {}
            if verdict not in ("pass", "deny"):
                reasons.append(
                    f"tool_call {tool!r} at {node}:{seq} has no recorded verdict — "
                    "an observation-only call cannot become a replayable gate decision"
                )
                continue
            for name, ref in arg_refs.items():
                value = values.get(ref)
                if value is None:
                    reasons.append(
                        f"tool_call {tool!r} at {node}:{seq} binds arg {name!r} to "
                        f"unknown value ref {ref!r} (no prior tool_result produced it)"
                    )
                    continue
                if not value.bound:
                    # the first concrete use is the only recorded projection of
                    # the value's content — the replay-authoritative decision_value
                    value.decision_value = args.get(name)
                    value.bound = True
            declared = [str(a) for a in (payload.get("driving_args") or ())]
            arg_refs.update(
                _model_values(node, seq, declared, arg_refs, args, payload, values)
            )
            normalized: dict[str, Any] = payload.get("normalized") or {}
            roles: dict[str, Any] = payload.get("roles") or {}
            call = _Call(
                node=node, seq=seq, tool=tool, args=dict(args), arg_refs=arg_refs,
                verdict=str(verdict),
                # A tool is an egress sink because the OPERATOR declared it one —
                # that is what axor-core's taint gate keys on. `destination_kind`
                # is the normalizer's structural guess from the tool's name, and
                # it does not know a deployment's vocabulary: `send_email`
                # normalises to `none`, so a run whose entire content was a
                # blocked exfiltration was refused here for containing "no
                # recorded egress consequence". A proxy-depth trace carries no
                # declared roles, so the structural signal still stands in.
                egress=bool(roles.get("egress_sink"))
                or str(normalized.get("destination_kind", "")) in _EGRESS_DESTINATIONS,
                declared_driving=declared,
                normalized=dict(normalized),
                driving_root=payload.get("driving_root"),
                floor_active=bool(payload.get("floor_active")),
            )
            calls.append(call)
            last_call[node] = call
            plan.append((node, "tool_call_intent", {"call": call}))
            plan.append((node, "gate_decision", {"call": call}))
        elif kind == "tool_result":
            tool = str(payload.get("tool", ""))
            ref = payload.get("value_ref")
            root: dict[str, Any] = payload.get("root") or {}
            produced: list[str] = []
            if ref is not None:
                ref = str(ref)
                if ref in values:
                    reasons.append(
                        f"tool_result at {node}:{seq} re-mints value ref {ref!r} — "
                        "the ledger would be ambiguous"
                    )
                    continue
                prior = last_call.get(node)
                derived = (
                    sorted(set(prior.arg_refs.values()))
                    if prior is not None and prior.tool == tool else []
                )
                values[ref] = _Value(
                    value_id=ref, tool=tool,
                    sources=[str(s) for s in (root.get("sources") or [])],
                    sensitive=bool(root.get("sensitive")),
                    derived_from=[d for d in derived if d in values],
                )
                produced = [ref]
            plan.append((node, "tool_result", {"tool": tool, "produces": produced}))
        elif kind in ("message_sent", "message_received"):
            if event.get("verdict") == "deny":
                reasons.append(
                    f"{kind} at {node}:{seq} records a message-gate DENY — trace/v1 "
                    "has no replayable representation for message-boundary decisions"
                )
                continue
            plan.append(
                (node, "message_send" if kind == "message_sent" else "message_recv", {})
            )
        elif kind == "node_spawned":
            plan.append((node, "spawn", {}))
        else:
            reasons.append(
                f"event kind {kind!r} at {node}:{seq} has no trace/v1 representation"
            )
    return calls, values, plan


def _tool_table(calls: list[_Call], values: dict[str, _Value]) -> dict[str, _Tool]:
    tools: dict[str, _Tool] = {}

    def entry(tool: str) -> _Tool:
        return tools.setdefault(tool, _Tool(tool=tool))

    for call in calls:
        t = entry(call.tool)
        for name, value in call.args.items():
            t.arg_types.setdefault(str(name), _json_type(value))
        for name in call.arg_refs:
            t.arg_types.setdefault(str(name), "string")
        if call.egress:
            t.egress = True
            # Prefer what the kernel DECLARED it gated on. Falling back to "every
            # argument that happens to be bound to a value ref" is a guess that
            # happens to hold only when the producer mints a ref per argument;
            # for a content-derivation producer it silently names whichever args
            # were tainted, which is the answer, not the question.
            t.driving_args.update(call.declared_driving or call.arg_refs.keys())
    for value in values.values():
        if not value.tool:
            continue  # model-composed, not produced by any tool (see _model_values)
        t = entry(value.tool)
        t.has_result = True
        # an untrusted value with no derivation edge entered the run HERE — the
        # producing tool is the source the injection fixture must target;
        # derived taint (e.g. a summary of it) is provenance, not a new source.
        if value.untrusted and not value.derived_from:
            t.untrusted_source = True
        if value.sensitive and not value.derived_from:
            t.sensitive_source = True
    return tools


# ── manifests / condition / scenario synthesis ───────────────────────────────


def _origin_sources(
    value: _Value, values: dict[str, _Value]
) -> list[dict[str, Any]]:
    """The external reads whose taint reached this value — its causal ROOT.

    trace/v1 is explicit that `sources` is "the transitive causal_root" while
    `derived_from` is "the immediate edge". This emitted neither: it named the
    value's own producing tool as an `external_read`, so a summary of an injected
    email claimed the injection entered at the SUMMARIZER — pointing an
    investigator at the tool that carried the taint rather than the one that let
    it in. A model-composed value, having no producing tool, emitted the dangling
    `tool_result:` instead.

    Walking `derived_from` back to the values that root a taint (untrusted with
    no parent) gives the set the schema asks for. A superset is allowed
    (over-taint); an omission is a soundness bug — so a cycle or a missing parent
    stops that branch rather than dropping the whole answer.
    """
    if not value.untrusted:
        return []
    roots: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    stack = [value.value_id]
    while stack:
        vid = stack.pop()
        if vid in seen:
            continue
        seen.add(vid)
        current = values.get(vid)
        if current is None or not current.untrusted:
            continue
        parents = [p for p in current.derived_from if p in values]
        if parents:
            stack.extend(parents)
        elif current.tool:
            roots[current.tool] = {
                "kind": "external_read",
                "origin_ref": f"tool_result:{current.tool}",
            }
    return [roots[tool] for tool in sorted(roots)]


def _manifest(t: _Tool) -> dict[str, Any]:
    effect: dict[str, Any] = {
        "default_class": "EXPORT" if t.egress else "READ",
        "driving_args": sorted(t.driving_args),
    }
    manifest: dict[str, Any] = {
        "schema_version": "tool-manifest/v1",
        "id": t.tool,
        "args_schema": {
            "type": "object",
            "properties": {name: {"type": kind} for name, kind in sorted(t.arg_types.items())},
            "required": [],
        },
        "effect": effect,
        "side_effecting": t.egress,
    }
    if t.has_result:
        manifest["result_schema"] = {
            "type": "object",
            "properties": {_RESULT_FIELD: {"type": "string"}},
        }
    if t.untrusted_source:
        manifest["untrusted_fields"] = [f"result.{_RESULT_FIELD}"]
    if t.sensitive_source:
        manifest["sensitive_fields"] = [f"result.{_RESULT_FIELD}"]
    return manifest


def _axor_core_pin() -> str:
    import axor_core

    return f"axor-core@{getattr(axor_core, '__version__', 'unknown')}"


def _replay_fidelity() -> dict[str, Any]:
    """The honest per-gate replay-fidelity statement carried with the incident.

    The exported condition pins Lab's reference taint_floor kernel. That verdict
    reproduces faithfully — it is a pure function of the value provenance the
    Control Plane records. Content-inspecting gates (ssrf on a URL, value_policy
    enum/range) need the payload bodies the Control Plane does not record
    (observations only), so they are not reproducible from this trace; an
    incident whose live verdict turned on such a gate is refused at export
    (``_decide_all``), never silently downgraded."""
    return {
        "backend": REFERENCE_KERNEL,
        "recorded_kernel": _axor_core_pin(),
        "reproducible_gates": [GATE_TAINT_FLOOR],
        "not_reproducible_gates": ["ssrf", "value_policy"],
        "note": (
            "Replayed under Lab's reference taint_floor kernel. The taint_floor "
            "verdict reproduces faithfully — it is a pure function of the value "
            "provenance the Control Plane records. Content-inspecting gates "
            "(ssrf, value_policy) are not reproducible from observation-only "
            "traces; an incident whose live verdict turned on such a gate is "
            "refused at export, never downgraded silently."
        ),
    }


def _condition(run_id: str) -> dict[str, Any]:
    kernel = REFERENCE_KERNEL
    return {
        "schema_version": "condition/v1",
        "id": f"cp-{run_id}",
        "label": "governed (control-plane recorded)",
        "enforcement": "on",
        "kernel": kernel,
        # the shared axor-core identity that actually produced the verdicts —
        # `kernel` pins the label-based replay backend (module docstring)
        "kernel_ref": _axor_core_pin(),
        "policy": dict(LAB_POLICY),
        "config_hash": condition_config_hash(kernel, LAB_POLICY),
    }


def _scenario(
    scenario_id: str,
    run_id: str,
    run_meta: dict[str, Any],
    tools: dict[str, _Tool],
    calls: list[_Call],
) -> dict[str, Any]:
    fixtures: dict[str, Any] = {}
    for t in tools.values():
        if t.untrusted_source:
            fixtures[t.tool] = {
                "result": {_RESULT_FIELD: "$injection"},
                "injection_placement": {"field": _RESULT_FIELD, "mode": "replace"},
            }
    evidence = run_meta.get("evidence") or []
    deviation = str(evidence[0].get("deviation", "")) if evidence else ""
    sink_call = _violation_call(calls, tools)
    sink_tool = tools[sink_call.tool]
    driving = sorted(sink_tool.driving_args)[0]
    violation = {
        "event": "tool_call",
        "tool": sink_call.tool,
        "where": {f"prov(args.{driving})": {"provenance_is": LABEL_UNTRUSTED}},
    }
    success_tool = next(
        (c.tool for c in calls if not tools[c.tool].egress), sink_call.tool
    )
    return {
        "schema_version": "scenario/v1",
        "name": scenario_id,
        "task": (
            f"Reproduce control-plane run {run_id}: the recorded agent task, "
            "replayed over the frozen trace"
        ),
        "inputs": {},
        "tools": [{"$ref": name} for name in tools],
        "fixtures": fixtures,
        "injection": {
            "text": (
                "[injected content — the control plane records provenance labels, "
                "not raw payload bodies; placement reconstructed from taint roots]"
            ),
            "goal": deviation or "recorded untrusted influence on an egress sink",
        },
        "violation": violation,
        "task_success": {"event": "tool_call", "tool": success_tool},
        "notes": (
            "Synthesized from a Control Plane run: fixtures/injection mark WHERE "
            "untrusted content entered (taint roots), not its unrecorded body."
        ),
    }


def _violation_call(calls: list[_Call], tools: dict[str, _Tool]) -> _Call:
    """The egress call the violation predicate names: prefer the recorded DENY.

    `build_incident_package` has already refused a run with no usable sink, and
    with the complete reason list rather than this one on its own — so reaching
    the raise below means those two checks disagree, which is a bug here and not
    a property of the run.
    """
    egress_calls = [c for c in calls if tools[c.tool].egress and tools[c.tool].driving_args]
    denied = [c for c in egress_calls if c.verdict == "deny"]
    if denied:
        return denied[0]
    if egress_calls:
        return egress_calls[0]
    raise LabExportError(
        ["internal: no egress call with a bound driving argument reached scenario "
         "synthesis, which the convertibility checks should have refused first"]
    )


# ── kernel decisions (the convertibility proof) ──────────────────────────────


def _decide_all(
    calls: list[_Call],
    tools: dict[str, _Tool],
    values: dict[str, _Value],
    reasons: list[str],
) -> None:
    """Recompute every call with the KERNEL'S OWN GATE and require it to agree
    with the recorded verdict — the exported trace must import with
    ``replay: match``.

    This used to be a hand-written mirror of ``lab_runner.kernel.Kernel.decide``,
    living inside the platform whose own contributing rules say the kernel is
    imported and never reimplemented. It reimplemented it because the obvious
    reading is that the real kernel needs content a recorded trace does not
    carry. It does not: ``ToolCallGovernor`` needs content because it is the
    LEDGER, and by the time a call is recorded the ledger's answer is already in
    the event — ``driving_root`` is a serialized ``CausalRoot`` and
    ``taint_gate`` takes nothing else about the value.
    """
    for call in calls:
        tool = tools.get(call.tool)
        if tool is None:  # unreachable: every call registers its tool
            continue
        try:
            decision = _kernel_decide(call, tool, values)
        except IncompleteRecord as exc:
            # Refusing to export beats guessing. A missing field here is one the
            # verdict turns on, and defaulting it to its permissive value is how
            # a recorded DENY comes back as a replayed ALLOW.
            reasons.append(
                f"{call.tool!r} at {call.node}:{call.seq} cannot be re-decided — {exc}"
            )
            continue
        recorded = "ALLOW" if call.verdict == "pass" else "DENY"
        if decision["verdict"] != recorded:
            reasons.append(
                f"recorded verdict {recorded} for {call.tool!r} at "
                f"{call.node}:{call.seq} would not reproduce under the kernel's "
                f"taint floor (recomputed {decision['verdict']}: {decision['reason']})"
            )
            continue
        call.decision = decision


def _driving_binding(
    call: _Call, tool: _Tool, values: dict[str, _Value]
) -> tuple[str | None, dict[str, Any] | None]:
    """The ledger id of the call's driving value, and the typed reason when
    there is none.

    A fail-closed decision must not invent a ``v_unresolved`` ledger id — the
    trace would then fail its own validation and the most interesting incidents
    would be unpublishable. ``driving_unresolved`` carries the reason instead.
    """
    driving_args = sorted(tool.driving_args)
    if not driving_args:
        return None, {"kind": "no_driving_args"}
    first = driving_args[0]
    bound = call.arg_refs.get(first)
    if bound is None:
        return None, {"kind": "unresolved_argument", "arg": first}
    return bound, None


def _recorded_root(call: _Call, tool: _Tool, values: dict[str, _Value]) -> dict[str, Any]:
    """The causal root the kernel decided this call on.

    Prefer what the kernel WROTE DOWN (``driving_root``): it is that kernel's own
    answer over the real content, computed by the ledger at the moment of the
    call. Falling back to the union of the driving args' recorded value roots is
    for a trace from a producer that predates the field; it is the same algebra
    (a causal root is the union of its inputs') so it over-taints at worst, which
    is the safe direction.
    """
    if call.driving_root is not None:
        return dict(call.driving_root)
    sources: set[str] = set()
    sensitive = False
    for arg in sorted(tool.driving_args):
        ref = call.arg_refs.get(arg)
        value = values.get(ref) if ref else None
        if value is None:
            continue
        sources.update(value.sources)
        sensitive = sensitive or value.sensitive
    return {"sources": sorted(sources), "sensitive": sensitive}


def _kernel_decide(
    call: _Call, tool: _Tool, values: dict[str, _Value]
) -> dict[str, Any]:
    """One call through ``axor_core.policy.gates.taint_gate``."""
    where = f"{call.node}:{call.seq}"
    normalized = normalized_from_record(call.tool, call.normalized, where=where)
    root = causal_root_from_record(_recorded_root(call, tool, values))
    driving_value_id, unresolved = _driving_binding(call, tool, values)

    denial = taint_gate(
        call.tool,
        normalized,
        root,
        floor_active=call.floor_active,
        # The operator's declaration of what exfiltrates. The gate also reads the
        # normalizer's structural guess, so passing the tool here only ADDS the
        # declared half — which is the half that knows a deployment's vocabulary
        # (`send_email` normalises to `none`).
        egress_sinks=frozenset({call.tool}) if tool.egress else frozenset(),
    )
    decision: dict[str, Any] = {
        "verdict": "ALLOW" if denial is None else "DENY",
        "gate": GATE_TAINT_FLOOR,
        "driving_value_id": driving_value_id,
        "reason": (
            denial.reason if denial is not None
            else f"kernel taint floor: no gate applies to {call.tool}"
        ),
    }
    if denial is not None:
        decision["projection"] = PROJECTION_UNTRUSTED
    if driving_value_id is None and unresolved is not None:
        decision["driving_unresolved"] = unresolved
    return decision


# ── trace assembly ───────────────────────────────────────────────────────────


def _trace(
    run_id: str,
    scenario_id: str,
    condition: dict[str, Any],
    scenario: dict[str, Any],
    calls: list[_Call],
    values: dict[str, _Value],
    plan: list[tuple[str, str, dict[str, Any]]],
) -> dict[str, Any]:
    seq_by_node: dict[str, int] = {}
    call_index: dict[int, int] = {}
    events: list[dict[str, Any]] = []
    for node, etype, extra in plan:
        seq = seq_by_node.get(node, 0)
        seq_by_node[node] = seq + 1
        event: dict[str, Any] = {"seq": seq, "node": node, "type": etype}
        if etype in ("tool_call_intent", "gate_decision"):
            call: _Call = extra["call"]
            call_id = call_index.setdefault(id(call), len(call_index))
            event["tool"] = call.tool
            event["call_id"] = f"{call.node}:c{call_id}"
            if etype == "tool_call_intent":
                event["arg_bindings"] = dict(call.arg_refs)
            else:
                event["decision"] = call.decision
        elif etype == "tool_result":
            event["tool"] = extra["tool"]
            event["produces_value_ids"] = list(extra["produces"])
        events.append(event)

    ledger: list[dict[str, Any]] = []
    for value in values.values():
        row: dict[str, Any] = {
            "value_id": value.value_id,
            "labels": value.labels(),
            "sources": _origin_sources(value, values),
            "decision_value": value.decision_value,
            "canonical_value_hash": content_hash(value.decision_value),
        }
        if isinstance(value.decision_value, str):
            row["preview"] = value.decision_value[:_PREVIEW_LEN]
        if value.derived_from:
            row["derived_from"] = list(value.derived_from)
        # `model_extraction` means an LLM produced the value from its context.
        # A tool-derived value is not that, and the enum has no entry for "a
        # tool computed it" — so it claims nothing rather than the wrong
        # constructor. `transformations` is optional; `sources` carries the
        # provenance either way.
        if value.model_composed:
            row["transformations"] = ["model_extraction"]
        ledger.append(row)

    return {
        "schema_version": "trace/v1",
        "trace_id": f"cp-{run_id}",
        "trial": {
            "run_id": run_id,
            "scenario_id": scenario_id,
            "condition_id": str(condition["id"]),
            # the CP does not seed model sampling; recorded as the null seed
            "seed": "0",
            "repeat_index": 0,
        },
        "producer": {
            "mode": "instrumented_endpoint",
            "provenance_fidelity": "explicit_flow_tracked",
            # must equal condition.kernel (bundle trace-metadata binding); the
            # producing axor-core build is in `runtime` / condition.kernel_ref
            "kernel_version": str(condition["kernel"]),
            "runtime": f"axor-control-plane/{_axor_core_pin()}",
        },
        "inputs_digest": world_digest(
            scenario.get("inputs", {}), scenario.get("fixtures", {})
        ),
        "events": events,
        "values": ledger,
    }
