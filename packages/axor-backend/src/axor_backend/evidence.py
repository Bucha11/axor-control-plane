"""Deriving EvidenceCases at the system of record.

The audit layers compare a fault log against the agent's answer, and who
assembled those two was the fault line in this product. The observe-only proxy
did it from its own in-memory run state, so `ToolAuditLayer` ran in exactly one
process — and a run that arrived here any other way carried real verdicts, a
real taint ledger, and nothing:

    the adapter path: POST /v1/ingest -> 202
       replay             -> 200, 3 steps, 1 recorded denial(s), gate='taint_floor'
       evidence on the run-> []
       regression corpus  -> 0 pins
       safe_to_ship       -> False

Two integrations, two answers to "does this run contain a discrepancy", and the
deeper one answered "nothing" — including for the must-block auto-pin, which
hangs off a deviation in the evidence, so the adapter path could not feed the
regression corpus at all.

The derivation is `axor_eval.audit.from_trace` now, imported and not restated —
the same posture this backend takes to the kernel's decoder and Sentinel's
attestation rules, and for the same reason: a platform that decided for itself
what an EvidenceCase is would be a second answer to a question Eval already
answers. This module is the trigger and the plumbing, not a second opinion.

It runs on INGEST, when a batch carries the run's claim. That is what makes it
path-independent: the proxy, an adapter-wrapped agent, a Lab deploy and a
direct POST all reach the same line, and none of them has to remember to ask.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("axor.backend")


def evidence_payload(case: Any) -> dict[str, Any]:  # noqa: ANN401 — an EvidenceCase
    """One case in the shape `POST /v1/runs/{id}/evidence` already stores.

    Byte-for-byte the proxy's `runs.evidence_to_dict`, because a case derived
    here and a case uploaded by a client must be the same row — the surfaces
    that read them cannot tell which path a run took, and should not have to.
    """
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
    import json

    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def batch_has_claim(lines: list[dict[str, Any]]) -> bool:
    """Whether this ingest batch is the one that finished a run.

    Checked on the BATCH rather than on the whole run, so re-deriving happens
    once — when the answer arrives — instead of on every subsequent append to a
    run that already had one.
    """
    from axor_eval.audit.from_trace import CLAIM

    return any(line.get("kind") == CLAIM for line in lines)


async def derive_for_run(
    store: Any, run_id: str, node_id: str, scenario: str,  # noqa: ANN401
) -> list[dict[str, Any]]:
    """Every case the run's own recorded trace supports, as stored rows.

    Reads the run back from the store rather than auditing the batch: a claim
    lands in the last batch, and the faults it is compared against arrived in
    earlier ones.
    """
    from axor_eval.audit.from_trace import evidence_from_trace

    lines = await store.run_events(run_id)
    cases = evidence_from_trace(
        lines, scenario=scenario, node_id=node_id, policy_name="recorded",
    )
    return [evidence_payload(case) for case in cases]
