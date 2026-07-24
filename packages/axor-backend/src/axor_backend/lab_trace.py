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
* A Lab trace is only convertible when it was recorded under the REAL axor-core
  kernel (``producer.kernel_version == axor-core@<installed build>``).  A trace
  recorded under Lab's label-based ``reference_taint_floor_kernel`` — a different
  engine — is NOT converted; the pin stays skipped with an honest reason.  A
  real-kernel pin under a DIFFERENT build than the one installed here is likewise
  left skipped (a build mismatch cannot claim reproduction).
* Even a build-matched trace is only accepted after a REPRODUCTION self-check:
  its converted events are replayed here through the real kernel under a config
  compiled from the package's own tool manifests, and the pin is accepted only
  if the recomputed verdict equals the recorded one.  A trace whose verdict would
  not reproduce (e.g. one whose real-kernel decision leaned on content the ledger
  does not carry) is left skipped rather than shipped as a false reproduction.
"""
from __future__ import annotations

from typing import Any

from axor_core.kernel.events import Event, EventKind, Verdict, event_to_json_line
from axor_core.kernel.replay import replay

from axor_backend.lab_export import content_hash
from axor_backend.replay_api import kernel_config_from_json

# Lab ledger labels (lab_export.LABEL_*): a value with this label carries an
# external source, i.e. it is integrity-tainted.
_LABEL_UNTRUSTED = "untrusted_derived"
_LABEL_SENSITIVE = "sensitive"
# Lab source kinds that do NOT introduce external taint (a literal / prompt input).
_TRUSTED_SOURCE_KINDS = frozenset({"constant", "prompt", "prompt_given"})
# A valid axor_core TaintSource string; the ledger's origin detail
# (which tool_result field) is not representable as a TaintSource, and the
# verdict depends only on is_tainted (any source present), so an external read
# maps to the honest generic external source.
_EXTERNAL_TAINT_SOURCE = "unknown_external"

# axor-core effect classes that make a tool an egress consequence (a sink taint
# can breach through). Mirrors lab_export._EGRESS_CLASSES.
_EGRESS_CLASSES = frozenset({"EXPORT", "EXEC"})


def installed_kernel_pin() -> str:
    """``axor-core@<version>`` for the axor-core build installed in THIS backend
    — the only real-kernel build whose Lab traces this CP may faithfully replay."""
    import axor_core

    return f"axor-core@{getattr(axor_core, '__version__', 'unknown')}"


def is_real_kernel_version(version: str) -> bool:
    return version.startswith("axor-core@")


def recorded_kernel_of(trace: dict[str, Any]) -> str:
    """The kernel a Lab trace was recorded under, from its producer block (the
    trace-metadata binding lab_export writes: ``producer.kernel_version``)."""
    producer: dict[str, Any] = trace.get("producer") or {}
    return str(producer.get("kernel_version", ""))


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
    decisions: dict[str, dict[str, Any]] = {
        str(e.get("call_id")): (e.get("decision") or {})
        for e in trace.get("events", [])
        if e.get("type") == "gate_decision"
    }
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
        registered.add(value_id)
        value = values.get(value_id)
        if value is None:
            return
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
            arg_bindings = {
                str(k): str(v) for k, v in (event.get("arg_bindings") or {}).items()
            }
            for value_id in arg_bindings.values():
                register(value_id, node)
            decision = decisions.get(str(event.get("call_id"))) or {}
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


def config_dict_from_manifests(manifests: list[dict[str, Any]]) -> dict[str, Any]:
    """A regression-report config (the direct-kernel shape
    ``kernel_config_from_json`` reads) compiled from the package's tool manifests.

    Egress sinks are the tools whose effect is an export consequence
    (default_class EXPORT/EXEC, or side_effecting); imperative sinks are the EXEC
    ones; each sink's driving args come from ``effect.driving_args``.  This is the
    same manifest→governor mapping the Lab used to produce the recorded verdict,
    so replaying the converted events under it reproduces that verdict."""
    egress: list[str] = []
    imperative: list[str] = []
    driving_args: dict[str, list[str]] = {}
    for manifest in manifests:
        if not isinstance(manifest, dict):
            continue
        tool = str(manifest.get("id", ""))
        if not tool:
            continue
        effect: dict[str, Any] = manifest.get("effect") or {}
        default_class = str(effect.get("default_class", ""))
        if default_class in _EGRESS_CLASSES or bool(manifest.get("side_effecting")):
            egress.append(tool)
        if default_class == "EXEC":
            imperative.append(tool)
        drivers = effect.get("driving_args")
        if isinstance(drivers, list) and drivers:
            driving_args[tool] = [str(a) for a in drivers]
    return {
        "egress_sinks": sorted(set(egress)),
        "imperative_sinks": sorted(set(imperative)),
        "driving_args": {t: v for t, v in sorted(driving_args.items())},
    }


def reproduces_recorded_verdict(
    events: list[Event], expected_verdict: str, manifests: list[dict[str, Any]]
) -> bool:
    """Replay the converted events under the real axor-core kernel with a config
    compiled from the package manifests and confirm the recomputed verdict of the
    (single) recorded tool call equals ``expected_verdict`` — the no-engine-swap
    proof that gates a pin as replayable."""
    config = kernel_config_from_json(config_dict_from_manifests(manifests))
    result = replay(events, config)
    recomputed: Verdict | None = None
    for step in result.steps:
        if step.event.kind is EventKind.TOOL_CALL:
            recomputed = step.reevaluated_verdict
    if recomputed is None:
        return False
    want = Verdict.DENY if expected_verdict == "DENY" else Verdict.PASS
    return recomputed is want and result.first_divergence is None


def events_to_lines(events: list[Event]) -> list[dict[str, Any]]:
    """Kernel-schema JSON dicts (the ``events.line`` column shape) for storage,
    so ``Store.run_events`` returns them and ``_events_for`` replays them."""
    import json

    return [json.loads(event_to_json_line(e)) for e in events]


def trace_matches_ref(trace: dict[str, Any], trace_ref: str) -> bool:
    """The embedded body must content-hash to the pin's recorded ``trace_ref``
    (the same RFC 8785 hash both repos pin) — a swapped/tampered body is refused."""
    try:
        return content_hash(trace) == trace_ref
    except Exception:  # noqa: BLE001 - canonicalization of an untrusted embedded body
        return False
