"""Degradation as pure recompute (spec decision #9).

level = max(severity(uncovered facts)). A fact is covered iff an unrevoked
attestation spans it. No transition table, no partial-descent ambiguity:
coverage changes, the function re-evaluates. Monotone over the fact sequence
(facts only accumulate; attestations are facts too); the level itself may
descend as coverage grows.
"""
from __future__ import annotations

from enum import IntEnum

from axor_kernel.events import Fact


class Level(IntEnum):
    NORMAL = 0
    CAUTIOUS = 1
    RESTRICTED = 2
    TERMINAL = 3


def _covered_ids(facts: dict[str, Fact]) -> frozenset[str]:
    revoked = {f.revokes for f in facts.values() if f.revokes is not None}
    covered: set[str] = set()
    for f in facts.values():
        if f.fact_type == "operator_attestation" and f.fact_id not in revoked:
            covered.update(f.covers)
    return frozenset(covered)


def compute_level(facts: dict[str, Fact]) -> Level:
    """Pure. Runtime path after every fact append; replay path — same code."""
    covered = _covered_ids(facts)
    worst = max(
        (f.severity for f in facts.values()
         if f.fact_type != "operator_attestation" and f.fact_id not in covered),
        default=0,
    )
    return Level(min(worst, int(Level.TERMINAL)))
