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

from axor_core.contracts.schemas import validate as validate_schema

from axor_backend.errors import BackendError
from axor_backend.lab_export import condition_config_hash
from axor_backend.limits import MAX_PINS_PER_PACKAGE

# The schema_version const, the verdict pair and the effect classes were all
# restated here as frozensets. They are the format's, and the format is
# `axor_core.contracts.schemas`; what is left below is this backend's own.
_PIN_PREFIX = "lab:"
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
    """Every problem with the uploaded package, or [].

    Two halves, kept apart on purpose.

    The SHAPE is `axor_core.contracts.schemas`' `cp-deploy` — the format's one
    definition, which the kernel owns and the Lab's exporter writes to. It used
    to be restated here by hand, forty lines of it, which is how a producer and
    a consumer in two repositories drift without either noticing.

    What stays below is what a schema cannot know: that a fingerprint matches the
    payload it claims to describe, and that a caller-chosen id fits the corpus
    key and does not repeat. Those are this platform's, and they are the ones
    that were actually wrong.
    """
    if not isinstance(package, dict):
        return ["package must be a JSON object (the cp-deploy.json content)"]
    errors: list[str] = list(validate_schema("cp-deploy", package))
    # An unverified TEMPLATE is schema-valid — `verified` is a boolean and the
    # Lab legitimately emits both — so refusing one is the consumer's policy, not
    # the format's. A config dump must never be mistaken for a proven handoff.
    if package.get("verified") is not True:
        errors.append(
            "package is not a finalized evidence-backed export (verified must be "
            "true; a template/unverified dump carries no evidence)"
        )
    kernel = package.get("kernel")
    policy = package.get("policy")
    recorded = package.get("config_hash")
    if isinstance(recorded, str) and recorded:
        if isinstance(kernel, str) and isinstance(policy, dict):
            try:
                recomputed = condition_config_hash(kernel, policy)
            except Exception as exc:  # noqa: BLE001 - untrusted input
                errors.append(f"kernel+policy are not canonicalizable: {exc}")
            else:
                if recorded != recomputed:
                    errors.append(
                        f"config_hash {recorded} does not match the exported "
                        f"kernel+policy ({recomputed}) — not the measured config"
                    )
    errors += _manifest_key_errors(package.get("tool_manifests"))
    errors += _pin_key_errors(package.get("regressions"))
    errors += _trace_body_errors(package.get("regression_traces"))
    return errors


def _manifest_key_errors(manifests: Any) -> list[str]:  # noqa: ANN401 - untrusted upload
    """Duplicate tool ids — the one manifest rule the schema cannot state.

    The CP turns the manifest list into a config keyed by tool id, so two
    manifests sharing an id become one entry and the loser's effect class,
    driving args and sensitive fields are gone. Everything else about a manifest
    is `tool-manifest/v1`'s to say, and it says it.
    """
    if not isinstance(manifests, list):
        return []
    errors: list[str] = []
    seen: set[str] = set()
    for i, manifest in enumerate(manifests):
        if not isinstance(manifest, dict):
            continue
        tool_id = manifest.get("id")
        if not isinstance(tool_id, str):
            continue
        if tool_id in seen:
            errors.append(
                f"tool_manifests[{i}]: duplicate tool id {tool_id!r} — the "
                "manifests are compiled into a config keyed by id, so one of "
                "the two would silently replace the other"
            )
        seen.add(tool_id)
    return errors


def _pin_key_errors(regressions: Any) -> list[str]:  # noqa: ANN401 - untrusted upload
    """What the corpus, not the format, requires of the pins.

    The schema types every pin field and bounds `trace_id` at 60. These are the
    three things it still cannot express: how many pins one request may ask this
    CP to replay, whether the id fits THIS backend's corpus key, and whether a
    pin's summary verdict agrees with the sequence it claims to summarize.
    """
    if not isinstance(regressions, list):
        return []
    errors: list[str] = []
    if len(regressions) > MAX_PINS_PER_PACKAGE:
        errors.append(
            f"{len(regressions)} regression pins exceeds the per-package ceiling "
            f"of {MAX_PINS_PER_PACKAGE} (AXOR_MAX_PINS_PER_PACKAGE) — each pin is "
            "hashed, converted and REPLAYED before this request answers"
        )
    budget = _PIN_RUN_ID_MAX - len(_PIN_PREFIX)
    seen: set[str] = set()
    for i, pin in enumerate(regressions):
        if not isinstance(pin, dict):
            continue
        where = f"regressions[{i}]"
        trace_id = pin.get("trace_id")
        if isinstance(trace_id, str) and trace_id:
            # The schema states this bound too, because a sender that only
            # learns it from a rejection has already built the package. But the
            # bound exists BECAUSE of this column, so this is where it is
            # measured: if the corpus key ever narrows, a schema-valid package
            # is still one this backend cannot store without truncating the id
            # — and a truncated key merges two pins into one row, losing
            # whichever side was written first.
            if len(trace_id) > budget:
                errors.append(
                    f"{where}: trace_id is {len(trace_id)} characters; the corpus "
                    f"stores it as `{_PIN_PREFIX}{{trace_id}}` in a "
                    f"{_PIN_RUN_ID_MAX}-character key, so at most {budget} fit. "
                    "Truncating would collide two pins into one."
                )
            if trace_id in seen:
                errors.append(
                    f"{where}: duplicate trace_id {trace_id!r} — two pins on one "
                    "id resolve to a single corpus row, and which side survives "
                    "depends on array order"
                )
            seen.add(trace_id)
        verdict = pin.get("expected_verdict")
        sequence = pin.get("expected_sequence")
        if (
            isinstance(sequence, list)
            and sequence
            and isinstance(verdict, str)
            and str(sequence[-1]) != verdict
        ):
            errors.append(
                f"{where}: expected_verdict {verdict} contradicts the final "
                f"recorded verdict {sequence[-1]}"
            )
    return errors


def _trace_body_errors(regression_traces: Any) -> list[str]:  # noqa: ANN401 - untrusted upload
    """A carried trace body must be filed under its own id.

    `regression_traces` is the map the CP replays pins from. The schema requires
    each body to HAVE a trace_id; only a reader holding both can check it is the
    key it was stored under, and a body under someone else's key would be
    replayed as evidence for the wrong pin.
    """
    if not isinstance(regression_traces, dict):
        return []
    return [
        f"regression_traces[{trace_id!r}]: body trace_id does not match its key"
        for trace_id, body in regression_traces.items()
        if isinstance(body, dict) and str(body.get("trace_id", "")) != str(trace_id)
    ]


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
