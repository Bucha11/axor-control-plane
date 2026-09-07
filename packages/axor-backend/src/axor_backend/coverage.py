"""What an operator attestation can discharge, and what the level becomes.

``covers`` on an attestation names FACT IDS. That is not a choice this file
makes — :class:`axor_core.kernel.events.Fact` says it in its own docstring, and
:mod:`axor_core.kernel.degradation` says what covering them does::

    level = max(severity(uncovered facts))

A fact is covered iff an unrevoked attestation spans it. There is no transition
table and no partial-descent ambiguity: coverage changes, the function
re-evaluates. Both functions are imported here — the plane must not hold a
second opinion about what an operator's signature bought.

Two facts about scope, and they are the same fact twice. A degradation fact is
minted by the trace bridge as ``deg_{seq}`` / ``quar_{seq}`` / ``stale_{seq}``,
and a value ref by the runtime's per-trace ledger as ``v_ext_1``. Both counters
restart at zero on every run, so a fact id and a value ref alike mean something
only inside the run that minted them — which is why an attestation naming either
must name its run.

The plane does not overwrite what the node reports. ``reported_level`` is the
node's own last word; ``level`` is what this recompute makes of the same facts
plus the coverage the operator has since added. A node applies the attestation
when the fact reaches it over its desired-state stream and converges — until
then the two differ, and the plane renders the divergence rather than hiding it
(protocol, section 5).
"""
from __future__ import annotations

from typing import Any

from axor_core.kernel.degradation import compute_level as kernel_level
from axor_core.kernel.degradation import covered_fact_ids
from axor_core.kernel.events import EventKind, Fact, fact_from_payload

ATTESTATION_FACT_TYPE = "operator_attestation"


def facts_of_run(events: list[Any]) -> dict[str, Fact]:
    """The FACT events of one run, keyed by fact id, in recorded order."""
    return {
        str(e.payload["fact_id"]): fact_from_payload(e.payload)
        for e in events
        if getattr(e, "kind", None) == EventKind.FACT and "fact_id" in (e.payload or {})
    }


def _causal_roots(events: list[Any]) -> dict[str, str]:
    """fact_id -> the branch the fact was recorded against.

    ``causal_root`` is a column on the kernel Event, not a field of ``Fact``, so
    it does not survive the fold — but it is the only thing that tells an
    operator WHICH branch a degradation fact is about, which is the whole
    question the attest button asks.
    """
    return {
        str(e.payload["fact_id"]): str(e.causal_root)
        for e in events
        if getattr(e, "kind", None) == EventKind.FACT
        and "fact_id" in (e.payload or {})
        and e.causal_root
    }


def coverage(
    events: list[Any],
    attestations: list[dict[str, Any]],
    reported_level: str = "NORMAL",
) -> dict[str, Any]:
    """The node's degradation, recomputed over its facts and their coverage.

    ``attestations`` are this run's operator attestations from the fact log —
    they live there, not in the trace, because the operator appends them to the
    plane rather than the node recording them.
    """
    run_facts = facts_of_run(events)
    plane_facts = {
        str(a["fact_id"]): fact_from_payload(a)
        for a in attestations if a.get("fact_id")
    }
    facts = {**run_facts, **plane_facts}
    covered = covered_fact_ids(facts)
    roots = _causal_roots(events)

    by_fact: dict[str, list[str]] = {}
    revoked = {f.revokes for f in facts.values() if f.revokes is not None}
    for fact in facts.values():
        if fact.fact_type != ATTESTATION_FACT_TYPE or fact.fact_id in revoked:
            continue
        for target in fact.covers:
            by_fact.setdefault(str(target), []).append(fact.operator or "")

    return {
        "reported_level": reported_level,
        "level": kernel_level(facts).name,
        "facts": [
            {
                "fact_id": fact.fact_id,
                "fact_type": fact.fact_type,
                "severity": fact.severity,
                "reason": fact.reason or "",
                "causal_root": roots.get(fact.fact_id),
                "covered_by": by_fact.get(fact.fact_id, []),
            }
            for fact in facts.values()
            if fact.fact_type != ATTESTATION_FACT_TYPE
        ],
        "covered": sorted(covered),
    }
