"""Lab trace/v1 → CP kernel events — the faithful inverse of ``lab_export``.

``lab_export.build_incident_package`` goes CP kernel events → a Lab
``trace/v1`` (recorded gate verdicts + a per-value taint ledger).  This module
inverts that mapping so a Lab regression pin, carried in an ``axor-cp-deploy/v1``
package, becomes a sequence of :class:`axor_core.kernel.events.Event` the
Control Plane can fold through the SAME ``axor_core.kernel.replay.replay`` the
regression corpus already uses.  A converted pin is a real, replayable corpus
trace — no longer a hash-only ``skipped`` row.

Faithfulness boundary (deliberately narrow, never widened by substitution):

* CP replay is EXPLICIT-FLOW: :func:`axor_core.kernel.replay.replay` re-derives a
  call's driving taint from the ``root.sources`` recorded on ``TOOL_RESULT``
  events and the ``arg_refs`` on ``TOOL_CALL`` events, then re-gates with the
  real ``axor_core.policy.gates`` cascade.  A Lab ``trace/v1`` ledger records
  exactly that explicit-flow evidence (per-value ``labels`` / ``sources``), so
  the conversion is a re-expression of the SAME evidence, not a re-derivation
  under a different engine.
* A trace is replayed only under the build that recorded it
  (``producer.kernel_version`` == this backend's axor-core).  A build mismatch
  cannot claim reproduction.  There is no longer a separate "is this a real
  kernel" test: axor-core is the only kernel, axor-lab imports it rather than
  carrying its own, and a trace naming anything else simply fails the build
  comparison with the same honest reason.
* The config is the one the package CARRIES (``runtime_configs``), checked
  against the ``runtime_config_hashes`` recorded beside it.  This module used to
  compile its own from the tool manifests, and it and the Lab's compiler
  disagreed on every point that mattered — ``side_effecting`` read as egress,
  ``effect.resolve`` never read, allowlist value-policies dropped entirely.  A
  compiled config is carried, not re-derived.
* Even a build-matched trace under its own config is only accepted after a
  REPRODUCTION self-check: its events are replayed here and every recorded
  verdict, in order, must come back.  A trace whose verdicts would not reproduce
  (e.g. one whose decision leaned on content the ledger does not carry) is left
  skipped rather than shipped as a false reproduction.
"""
from __future__ import annotations

import json
from typing import Any

from axor_core.contracts.schemas import validate as validate_schema
from axor_core.kernel.events import Event, EventKind, Verdict, event_to_json_line
from axor_core.kernel.replay import replay

from axor_backend.lab_export import content_hash
from axor_backend.replay_api import KernelConfig, ValuePredicate

# Lab ledger labels (lab_export.LABEL_*): a value with this label carries an
# external source, i.e. it is integrity-tainted.
_LABEL_UNTRUSTED = "untrusted_derived"
_LABEL_SENSITIVE = "sensitive"
# `sources` is the closed constructor set trace/v1 inducts over:
# constant | external_read | mint | parse | cross_process_in. Only `constant` is
# a trusted root. `mint` ("freshly created by the agent — per policy") is treated
# as tainted: over-taint is the safe direction, and a value the CP cannot vouch
# for is not one it may quietly clear.
# This set used to read {constant, prompt, prompt_given} — two entries that are
# not source kinds at all (`prompt_given` is a LABEL), so one of its three
# members did anything.
_TRUSTED_SOURCE_KINDS = frozenset({"constant"})
# A valid axor_core TaintSource string; the ledger's origin detail
# (which tool_result field) is not representable as a TaintSource, and the
# verdict depends only on is_tainted (any source present), so an external read
# maps to the honest generic external source.
_EXTERNAL_TAINT_SOURCE = "unknown_external"


class TraceNotConvertible(ValueError):
    """The embedded trace cannot be converted into kernel events faithfully.

    Raised rather than returning a partial conversion: a trace missing the
    correlation a decision needs, or naming a value its own ledger does not
    hold, does not produce "most of" a replay — it produces a replay of
    something else.
    """


def installed_kernel_pin() -> str:
    """``axor-core@<version>`` for the axor-core build installed in THIS backend
    — the only build whose Lab traces this CP may faithfully replay."""
    import axor_core

    return f"axor-core@{getattr(axor_core, '__version__', 'unknown')}"


def recorded_kernel_of(trace: dict[str, Any]) -> str:
    """The kernel a Lab trace was recorded under, from its producer block (the
    trace-metadata binding lab_export writes: ``producer.kernel_version``)."""
    producer: dict[str, Any] = trace.get("producer") or {}
    return str(producer.get("kernel_version", ""))


def trace_schema_errors(trace: Any) -> list[str]:  # noqa: ANN401 - untrusted upload
    """Every way the embedded body is not a ``trace/v1``, or [].

    The bodies arrive inside an upload and used to go straight into the
    converter, which read `values`, `events`, `arg_bindings` and `decision` off
    whatever it was handed. A binding naming a value the ledger does not contain
    was silently skipped, and the call replayed with less taint than the trace
    recorded — the one direction of error that turns a DENY into a PASS.
    """
    return list(validate_schema("trace", trace))


def _value_root(value: dict[str, Any]) -> dict[str, Any] | None:
    """The CP causal-root payload for a Lab ledger value, or None when the value
    carries no taint (a trusted/constant value needs no registration)."""
    labels = value.get("labels") or []
    sources = value.get("sources") or []
    has_external = any(
        str((s or {}).get("kind")) not in _TRUSTED_SOURCE_KINDS for s in sources
    )
    tainted = _LABEL_UNTRUSTED in labels or has_external
    sensitive = _LABEL_SENSITIVE in labels
    if not (tainted or sensitive):
        return None
    return {
        "sources": [_EXTERNAL_TAINT_SOURCE] if tainted else [],
        "sensitive": sensitive,
    }


def _decisions_by_call_id(trace: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """``call_id`` → its gate decision, refusing anything ambiguous.

    ``call_id`` is OPTIONAL in trace/v1, and its own description says it exists
    "so replay pairs them by id (not just node order) and can detect a missing or
    duplicated decision". This used to be a dict comprehension keyed on
    ``str(event.get("call_id"))``: every event without one collapsed into the
    single key ``"None"`` and every duplicate silently overwrote its
    predecessor, so a two-call trace gave BOTH calls the last decision recorded
    — an ALLOW came back carrying someone else's DENY.
    """
    decisions: dict[str, dict[str, Any]] = {}
    for event in trace.get("events", []):
        if event.get("type") != "gate_decision":
            continue
        call_id = event.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            raise TraceNotConvertible(
                "a gate_decision carries no call_id, so it cannot be paired with "
                "the call it decided; trace/v1 makes call_id optional but a "
                "replay of an unpaired decision is a replay of a guess"
            )
        if call_id in decisions:
            raise TraceNotConvertible(
                f"two gate_decision events share call_id {call_id!r} — one of the "
                "two verdicts would be discarded, and which depends on array order"
            )
        decisions[call_id] = event.get("decision") or {}
    return decisions


def lab_trace_to_events(trace: dict[str, Any]) -> list[Event]:
    """Convert one Lab ``trace/v1`` into CP kernel events (replay-ready).

    The inversion of ``lab_export`` per event kind:

    * a Lab ``tool_result`` (``produces_value_ids``) → one ``TOOL_RESULT`` per
      produced value that carries taint, each registering ``value_ref`` under the
      ledger's recorded ``root`` (sources / sensitive) — the same fold input
      ``lab_export`` read out of the CP ``root`` payload;
    * a Lab ``tool_call_intent`` + its matching ``gate_decision`` (joined by
      ``call_id``) → one ``TOOL_CALL`` carrying ``arg_refs`` (the ledger value
      each argument bound to) and the recorded verdict.  Any tainted argument
      value not yet registered by a ``tool_result`` (a model-derived value the
      ledger records but no tool minted) is registered just-in-time from the
      ledger, so the driving taint the call keyed on is present when replay folds
      it — exactly the union taint the Lab ledger already attributes to it.

    Ordering is preserved per node (CP replay folds each node's own ``seq``
    order); a fresh monotonic ``seq`` is assigned per node."""
    values = {str(v["value_id"]): v for v in trace.get("values", [])}
    decisions = _decisions_by_call_id(trace)
    events: list[Event] = []
    seq_by_node: dict[str, int] = {}
    registered: set[str] = set()

    def next_seq(node: str) -> int:
        s = seq_by_node.get(node, 0)
        seq_by_node[node] = s + 1
        return s

    def register(value_id: str, node: str) -> None:
        if value_id in registered:
            return
        value = values.get(value_id)
        if value is None:
            raise TraceNotConvertible(
                f"value_id {value_id!r} is bound by an event but absent from the "
                "ledger; skipping it would replay the call with less taint than "
                "the trace recorded"
            )
        registered.add(value_id)
        root = _value_root(value)
        if root is None:
            return  # trusted/constant value: nothing to fold, no event needed
        events.append(Event(
            seq=next_seq(node), node_id=node, kind=EventKind.TOOL_RESULT, ts="lab",
            payload={"tool": str(value.get("tool", "")), "value_ref": value_id,
                     "root": root},
        ))

    for event in trace.get("events", []):
        etype = str(event.get("type", ""))
        node = str(event.get("node") or "root")
        if etype == "tool_result":
            for value_id in event.get("produces_value_ids") or []:
                register(str(value_id), node)
        elif etype == "tool_call_intent":
            call_id = event.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                raise TraceNotConvertible(
                    "a tool_call_intent carries no call_id, so its gate decision "
                    "cannot be identified"
                )
            if call_id not in decisions:
                raise TraceNotConvertible(
                    f"tool_call_intent {call_id!r} has no gate_decision; a call "
                    "with no recorded verdict has nothing to reproduce"
                )
            arg_bindings = {
                str(k): str(v) for k, v in (event.get("arg_bindings") or {}).items()
            }
            for value_id in arg_bindings.values():
                register(value_id, node)
            decision = decisions[call_id]
            verdict_str = str(decision.get("verdict", ""))
            verdict = (
                Verdict.PASS if verdict_str == "ALLOW"
                else Verdict.DENY if verdict_str == "DENY" else None
            )
            args = {
                arg: (values.get(vid) or {}).get("decision_value")
                for arg, vid in arg_bindings.items()
            }
            events.append(Event(
                seq=next_seq(node), node_id=node, kind=EventKind.TOOL_CALL, ts="lab",
                gate=(str(decision.get("gate")) if verdict is Verdict.DENY else None),
                verdict=verdict,
                payload={"tool": str(event.get("tool", "")), "args": args,
                         "arg_refs": arg_bindings},
            ))
        # gate_decision is folded into its TOOL_CALL above; other Lab event kinds
        # (message_send/recv, spawn) carry no replayable gate for a size-1 pin and
        # are intentionally not emitted here.
    return events


def kernel_config_from_governor_config(config: dict[str, Any]) -> KernelConfig:
    """Read a carried ``runtime_configs`` body into a :class:`KernelConfig`.

    A translation between two representations of one control, not a second
    compiler: every field is taken as written. ``untrusted_sources`` /
    ``untrusted_fields`` have no counterpart and none is invented — they tell a
    LIVE governor which tool results to mint taint from, while replay reads the
    taint already recorded on each ``TOOL_RESULT``.
    """
    driving: dict[str, Any] = config.get("driving_args") or {}
    value_policies: dict[str, list[ValuePredicate]] = {}
    for tool, per_arg in (config.get("value_policies") or {}).items():
        if not isinstance(per_arg, dict):
            continue
        value_policies[str(tool)] = [
            ValuePredicate(arg=str(arg), kind="enum",
                           allowed=frozenset(str(v) for v in (rule or {}).get("enum", ())))
            for arg, rule in per_arg.items()
        ]
    return KernelConfig(
        egress_sinks=frozenset(str(t) for t in config.get("egress_sinks", ())),
        imperative_sinks=frozenset(str(t) for t in config.get("imperative_sinks", ())),
        value_policies=value_policies,
        driving_args={str(t): frozenset(str(a) for a in args)
                      for t, args in driving.items()},
    )


def reproduces_recorded_sequence(
    events: list[Event], expected_sequence: list[str], config: KernelConfig,
) -> bool:
    """Replay the converted events under the config the trace RAN under and
    confirm the recomputed verdicts equal the pinned sequence, in order.

    The pin carries the whole ordered sequence precisely so a multi-call trace
    cannot match on its final verdict alone; this used to take the scalar
    ``expected_verdict`` and compare only the last TOOL_CALL, leaving the
    sequence read once by the validator and never used again.
    """
    result = replay(events, config)
    recomputed = [
        step.reevaluated_verdict for step in result.steps
        if step.event.kind is EventKind.TOOL_CALL
    ]
    if len(recomputed) != len(expected_sequence):
        return False
    want = [Verdict.DENY if v == "DENY" else Verdict.PASS for v in expected_sequence]
    return recomputed == want and result.first_divergence is None


def events_to_lines(events: list[Event]) -> list[dict[str, Any]]:
    """Kernel-schema JSON dicts (the ``events.line`` column shape) for storage,
    so ``Store.run_events`` returns them and ``_events_for`` replays them."""
    return [json.loads(event_to_json_line(e)) for e in events]


def trace_matches_ref(trace: dict[str, Any], trace_ref: str) -> bool:
    """The embedded body must content-hash to the pin's recorded ``trace_ref``
    (the same RFC 8785 hash both repos pin) — a swapped/tampered body is refused."""
    try:
        return content_hash(trace) == trace_ref
    except Exception:  # noqa: BLE001 - canonicalization of an untrusted embedded body
        return False
