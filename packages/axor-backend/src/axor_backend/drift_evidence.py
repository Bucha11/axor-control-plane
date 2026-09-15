"""The behavioral-drift projection — and the one place it can be made.

axor-probe writes a battery into an eval feed (`integration.eval`); axor-eval
grades that feed into a BEHAVIORAL_DRIFT EvidenceCase
(`audit.behavioral_audit.BehavioralIntegrityAudit`). Both ends shipped. Nothing
called either: measured across all seven repositories, `feed_audit` and
`BehavioralIntegrityAudit` had no caller outside their own tests, so a node
posting DRIFT_DETECTED with 3 escapes out of 5 left one `probe_reports` row and
a notification carrying the bare verdict string.

Neither library may call the other — P-34 is a real dependency direction, and
axor-eval declares only axor-core. This backend already imports both (the
probe vocabulary for the plane door, `axor_eval.audit.from_trace` for run
evidence), which makes it the only component that CAN hold this wire, and the
only one that can notice when the two vocabularies drift apart.

**What this projection must never do.** ui-spec §8.2: the health check is not
an Eval metric and must not be blended into Scenario Delta or Core scores.
axor-eval already holds that end — BEHAVIORAL_DRIFT is not in CORE_DEVIATIONS,
so `ScenarioResult.core_cases` excludes it whatever its confidence. This end
holds the other: the case is derived on READ, beside the health report, and
never reaches `store_evidence`. That is not squeamishness about a column. The
run-evidence path ends in the must-block auto-pin and `safe_to_ship`, and a
battery has no run — wiring drift through it would mean inventing one, which
is exactly the blend the spec forbids. `test_drift_is_not_an_eval_metric`
asserts it stays out.

Derived rather than stored because it is a projection, not a fact: pure,
cheap, and correct for reports written before this existed. The stored row
remains what the node posted.
"""
from __future__ import annotations

from typing import Any

from axor_eval.audit.behavioral_audit import BehavioralIntegrityAudit
from axor_probe.integration.eval import audit_payload


def drift_case(health: dict[str, Any] | None) -> dict[str, Any] | None:
    """The graded case for one posted health payload, or None.

    None for a verdict that is not a deviation (CONSISTENT, INCONCLUSIVE) and
    for a node that has never reported — which is not the same as a clean
    grade, and renders as an absence on both surfaces.

    The re-keying into the feed shape is axor-probe's `audit_payload`, not a
    mapping written here: the panel projection calls the drift score
    `max_drift_score_uncalibrated` so nothing thresholds it, the feed calls it
    `max_drift_score`, and handing the panel's dict to the grader unchanged
    drops it silently. Measured on a CONSISTENCY_ANOMALY report scoring 0.7,
    that is a confidence of 0.05 — the floor — instead of 0.35.
    """
    if not health:
        return None
    case = BehavioralIntegrityAudit().evaluate(audit_payload(health))
    if case is None:
        return None
    return {
        "deviation": case.deviation.value,
        # The tier, which is the point of grading at all. Escape-backed drift
        # is a canary/structural fact about the probe output, so it is
        # deterministic at 1.0; a consistency anomaly is judge-graded and
        # discounted again when the battery is uncalibrated. The plane used to
        # keep only the verdict string, so those two paged identically.
        "verdict_source": case.verdict_source,
        "confidence": case.confidence,
        "observed_reality": dict(case.observed_reality),
        "agent_claim": case.agent_claim,
        # Stated in the payload, not just in a comment: whoever renders this
        # should not have to know the deviation table to know it is not a score.
        "experimental": True,
        "in_integrity_score": False,
    }
