"""Lab → CP: accept an ``axor-cp-deploy/v1`` package (the earned bridge).

The Lab's ``export-cp`` emits the validated policy + tool manifests +
validated regression pins as ``cp-deploy.json`` (lab_runner/cp_export.py).
This module is the receiving side: structural validation, refusal of anything
that is not a FINALIZED evidence-backed export (``verified: true`` — a
template dump carries no evidence and is refused), a stored package record,
and regression pins folded into the CP corpus with their source marked
``lab:{package_id}``.

The backend persists and fans out; it never re-interprets governance — the
policy/manifests are stored verbatim for the operator to apply, and the pins
land in the same corpus ``POST /v1/pins`` feeds.  The ``config_hash`` is
recomputed here over the exported kernel+policy (the same RFC 8785
canonicalization both repos pin with shared vectors), so a package whose
recorded fingerprint does not match its own payload is refused rather than
stored as "the measured config".
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from axor_backend.errors import BackendError
from axor_backend.lab_export import condition_config_hash
from axor_backend.limits import MAX_PINS_PER_PACKAGE

CP_DEPLOY_SCHEMA = "axor-cp-deploy/v1"
_PIN_PREFIX = "lab:"
_VALID_VERDICTS = frozenset({"ALLOW", "DENY"})
_EFFECT_CLASSES = frozenset({"READ", "WRITE", "EXPORT", "EXEC"})
# pins land in the runs-keyed corpus table; run_id column is 64 chars
_PIN_RUN_ID_MAX = 64


class LabDeployRejected(BackendError):
    """The uploaded cp-deploy package is invalid; ``reasons`` lists everything."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("cp-deploy package rejected: " + "; ".join(reasons))
        self.reasons: tuple[str, ...] = tuple(reasons)


@dataclass(frozen=True)
class PinPlan:
    run_id: str
    side: str  # must_block | must_pass
    label: str


@dataclass(frozen=True)
class PinDeployPlan:
    """One corpus pin a package creates, plus whether its carried trace can be
    faithfully replayed on this CP (real axor-core kernel, build-matched, verdict
    reproduces) or must stay ``skipped`` with an honest reason."""

    run_id: str
    side: str  # must_block | must_pass
    label: str
    trace_id: str
    replayable: bool
    reason: str  # "" when replayable, else the skip reason
    event_lines: list[dict[str, Any]]  # kernel-schema lines when replayable, else []


def package_id_of(package: dict[str, Any]) -> str:
    """Deterministic id for a deploy package.

    Plain sorted-key JSON (not JCS): the package legitimately carries floats
    (bridge-analysis p-values / intervals) that the float-free governance
    canonicalizer rejects, and this id is an internal storage key, not a
    cross-language governance fingerprint."""
    digest = hashlib.sha256(
        json.dumps(package, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return f"labpkg_{digest[:16]}"


def validate_cp_deploy(package: Any) -> list[str]:  # noqa: ANN401 - untrusted upload
    """Every structural problem with the uploaded package, or []."""
    if not isinstance(package, dict):
        return ["package must be a JSON object (the cp-deploy.json content)"]
    errors: list[str] = []
    if package.get("schema_version") != CP_DEPLOY_SCHEMA:
        errors.append(
            f"schema_version {package.get('schema_version')!r} is not {CP_DEPLOY_SCHEMA!r}"
        )
    # finalized state only: an evidence-backed export sets verified=true; a
    # template (export_cp_template) is an unverified config dump and is refused
    if package.get("verified") is not True:
        errors.append(
            "package is not a finalized evidence-backed export (verified must be "
            "true; a template/unverified dump carries no evidence)"
        )
    kernel = package.get("kernel")
    if not isinstance(kernel, str) or not kernel:
        errors.append("kernel must be a non-empty string")
    policy = package.get("policy")
    if not isinstance(policy, dict):
        errors.append("policy must be an object")
    recorded = package.get("config_hash")
    if not isinstance(recorded, str) or not recorded:
        errors.append("config_hash must be a non-empty string")
    elif isinstance(kernel, str) and isinstance(policy, dict):
        try:
            recomputed = condition_config_hash(kernel, policy)
        except Exception as exc:  # noqa: BLE001 - canonicalization of untrusted input
            errors.append(f"kernel+policy are not canonicalizable: {exc}")
        else:
            if recorded != recomputed:
                errors.append(
                    f"config_hash {recorded} does not match the exported "
                    f"kernel+policy ({recomputed}) — not the measured config"
                )
    if not isinstance(package.get("parametric_config_hash"), str) or not package.get(
        "parametric_config_hash"
    ):
        errors.append("parametric_config_hash must be a non-empty string")
    errors += _manifest_errors(package.get("tool_manifests"))
    errors += _regression_errors(package.get("regressions"))
    errors += _regression_traces_errors(package.get("regression_traces"))
    source = package.get("source")
    if not isinstance(source, dict) or not source.get("bundle_id") or not source.get(
        "condition_id"
    ):
        errors.append("source must be an object naming bundle_id and condition_id")
    return errors


def _manifest_errors(manifests: Any) -> list[str]:  # noqa: ANN401 - untrusted upload
    if not isinstance(manifests, list) or not manifests:
        return ["tool_manifests must be a non-empty list"]
    errors: list[str] = []
    seen: set[str] = set()
    for i, manifest in enumerate(manifests):
        where = f"tool_manifests[{i}]"
        if not isinstance(manifest, dict):
            errors.append(f"{where} is not an object")
            continue
        if manifest.get("schema_version") != "tool-manifest/v1":
            errors.append(f"{where}: schema_version is not 'tool-manifest/v1'")
        tool_id = manifest.get("id")
        if not isinstance(tool_id, str) or not tool_id:
            errors.append(f"{where}: id must be a non-empty string")
        elif tool_id in seen:
            errors.append(f"{where}: duplicate tool id {tool_id!r}")
        else:
            seen.add(tool_id)
        if not isinstance(manifest.get("args_schema"), dict):
            errors.append(f"{where}: args_schema must be an object")
        effect = manifest.get("effect")
        if not isinstance(effect, dict):
            errors.append(f"{where}: effect must be an object")
        else:
            if str(effect.get("default_class")) not in _EFFECT_CLASSES:
                errors.append(
                    f"{where}: effect.default_class {effect.get('default_class')!r} "
                    f"is not one of {sorted(_EFFECT_CLASSES)}"
                )
            if not isinstance(effect.get("driving_args"), list):
                errors.append(f"{where}: effect.driving_args must be a list")
        if not isinstance(manifest.get("side_effecting"), bool):
            errors.append(f"{where}: side_effecting must be a boolean")
    return errors


def _regression_errors(regressions: Any) -> list[str]:  # noqa: ANN401 - untrusted upload
    if regressions is None:
        return ["regressions must be a list (may be empty)"]
    if not isinstance(regressions, list):
        return ["regressions must be a list"]
    errors: list[str] = []
    if len(regressions) > MAX_PINS_PER_PACKAGE:
        errors.append(
            f"{len(regressions)} regression pins exceeds the per-package ceiling "
            f"of {MAX_PINS_PER_PACKAGE} (AXOR_MAX_PINS_PER_PACKAGE) — each pin is "
            "hashed, converted and REPLAYED before this request answers"
        )
    seen_trace_ids: set[str] = set()
    for i, pin in enumerate(regressions):
        where = f"regressions[{i}]"
        if not isinstance(pin, dict):
            errors.append(f"{where} is not an object")
            continue
        trace_id = pin.get("trace_id")
        if not isinstance(trace_id, str) or not trace_id:
            errors.append(f"{where}: trace_id must be a non-empty string")
        else:
            # A pin is stored under `lab:{trace_id}` in a 64-char column, and the
            # id used to be TRUNCATED to fit. Two pins agreeing on their first
            # 60 characters then became one row, the second overwriting the
            # first — so a package pinning both sides of a case could land only
            # the must_pass side, in the corpus whose entire premise is that a
            # config blocking everything must not pass. The route reported both
            # as created. Refuse the id instead of silently mangling it.
            budget = _PIN_RUN_ID_MAX - len(_PIN_PREFIX)
            if len(trace_id) > budget:
                errors.append(
                    f"{where}: trace_id is {len(trace_id)} characters; the corpus "
                    f"stores it as `{_PIN_PREFIX}{{trace_id}}` in a "
                    f"{_PIN_RUN_ID_MAX}-character key, so at most {budget} fit. "
                    "Truncating would collide two pins into one."
                )
            if trace_id in seen_trace_ids:
                errors.append(
                    f"{where}: duplicate trace_id {trace_id!r} — two pins on one "
                    "id resolve to a single corpus row, and which side survives "
                    "depends on array order"
                )
            seen_trace_ids.add(trace_id)
        verdict = pin.get("expected_verdict")
        if verdict not in _VALID_VERDICTS:
            errors.append(
                f"{where}: expected_verdict {verdict!r} is not one of "
                f"{sorted(_VALID_VERDICTS)}"
            )
        ref = pin.get("trace_ref")
        if not isinstance(ref, str) or not ref.startswith("sha256:"):
            errors.append(f"{where}: trace_ref must be a sha256: content hash")
        sequence = pin.get("expected_sequence")
        if not isinstance(sequence, list) or not sequence:
            errors.append(f"{where}: expected_sequence must be a non-empty list")
        elif any(str(v) not in _VALID_VERDICTS for v in sequence):
            errors.append(f"{where}: expected_sequence contains a non-verdict entry")
        elif verdict in _VALID_VERDICTS and str(sequence[-1]) != verdict:
            errors.append(
                f"{where}: expected_verdict {verdict} contradicts the final "
                f"recorded verdict {sequence[-1]}"
            )
    return errors


def _regression_traces_errors(
    regression_traces: Any,  # noqa: ANN401 - untrusted upload
) -> list[str]:
    """``regression_traces`` is an ADDITIVE map {trace_id: trace body} the Lab
    export embeds so the CP can REPLAY the pins (not merely record their hashes).
    It is optional for backward compatibility — a package without it is valid, its
    pins simply stay skipped — but when present it must be a JSON object, and every
    body must be an object naming its own trace_id."""
    if regression_traces is None:
        return []
    if not isinstance(regression_traces, dict):
        return ["regression_traces must be an object mapping trace_id -> trace body"]
    errors: list[str] = []
    for trace_id, body in regression_traces.items():
        where = f"regression_traces[{trace_id!r}]"
        if not isinstance(body, dict):
            errors.append(f"{where} is not an object")
            continue
        if str(body.get("trace_id", "")) != str(trace_id):
            errors.append(f"{where}: body trace_id does not match its key")
    return errors


def _pin_run_id(trace_id: str) -> str:
    """The corpus key for a Lab pin.

    No truncation: `validate_cp_deploy` refuses a trace_id that would not fit,
    because a key silently cut to length turns two pins into one row and loses
    whichever side was written first.
    """
    return f"{_PIN_PREFIX}{trace_id}"


def pin_plans(package: dict[str, Any], package_id: str) -> list[PinPlan]:
    """The corpus pins a validated package creates: must_block for DENY pins,
    must_pass for ALLOW, keyed ``lab:{trace_id}`` and labelled with the source
    package so the Regression surface can attribute them."""
    plans: list[PinPlan] = []
    for pin in package.get("regressions", []):
        side = "must_block" if pin["expected_verdict"] == "DENY" else "must_pass"
        plans.append(PinPlan(
            run_id=_pin_run_id(pin["trace_id"]),
            side=side,
            label=f"lab:{package_id}",
        ))
    return plans


def deploy_plans(package: dict[str, Any], package_id: str) -> list[PinDeployPlan]:
    """Plan the corpus pins a validated package creates AND decide, per pin,
    whether its carried trace can be faithfully replayed on this CP.

    A pin is REPLAYABLE only when the package embeds the pin's trace body, the
    body content-hashes to the pin's ``trace_ref``, the trace was recorded under
    the REAL axor-core kernel matching THIS backend's installed build, the trace
    converts to kernel events, AND replaying those events under a config compiled
    from the package manifests reproduces the pinned verdict.  Otherwise the pin
    is created exactly as before and left ``skipped`` with an honest reason — the
    engine is never substituted to force a replay."""
    from axor_backend.lab_trace import (
        events_to_lines,
        installed_kernel_pin,
        is_real_kernel_version,
        lab_trace_to_events,
        recorded_kernel_of,
        reproduces_recorded_verdict,
        trace_matches_ref,
    )

    traces: dict[str, Any] = package.get("regression_traces") or {}
    manifests: list[dict[str, Any]] = package.get("tool_manifests") or []
    installed = installed_kernel_pin()
    plans: list[PinDeployPlan] = []
    for pin in package.get("regressions", []):
        trace_id = str(pin["trace_id"])
        side = "must_block" if pin["expected_verdict"] == "DENY" else "must_pass"
        run_id = _pin_run_id(trace_id)
        label = f"lab:{package_id}"

        def skip(reason: str) -> PinDeployPlan:
            return PinDeployPlan(run_id=run_id, side=side, label=label,
                                 trace_id=trace_id, replayable=False,
                                 reason=reason, event_lines=[])

        trace = traces.get(trace_id)
        if not isinstance(trace, dict):
            plans.append(skip("package carries no trace body for this pin"))
            continue
        if not trace_matches_ref(trace, str(pin.get("trace_ref", ""))):
            plans.append(skip("embedded trace body does not match the pin trace_ref"))
            continue
        recorded_kernel = recorded_kernel_of(trace)
        if not is_real_kernel_version(recorded_kernel):
            plans.append(skip(
                f"recorded under reference kernel ({recorded_kernel!r}); CP replays "
                "the real axor-core kernel and will not substitute it"
            ))
            continue
        if recorded_kernel != installed:
            plans.append(skip(
                f"kernel build mismatch (recorded {recorded_kernel!r}, this CP runs "
                f"{installed!r}); refusing to claim reproduction under a different build"
            ))
            continue
        try:
            events = lab_trace_to_events(trace)
        except Exception as exc:  # noqa: BLE001 - untrusted embedded body
            plans.append(skip(f"trace did not convert to kernel events: {exc}"))
            continue
        if not reproduces_recorded_verdict(events, str(pin["expected_verdict"]), manifests):
            plans.append(skip(
                "recorded verdict would not reproduce under axor-core replay with the "
                "package's manifests (leaving skipped rather than faking reproduction)"
            ))
            continue
        plans.append(PinDeployPlan(
            run_id=run_id, side=side, label=label, trace_id=trace_id,
            replayable=True, reason="", event_lines=events_to_lines(events),
        ))
    return plans
