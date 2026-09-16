"""Operator attestations — recorded here, given meaning by Sentinel.

The Control Plane RECORDS an attestation: it is an operator action on the
plane, signed with the operator keyring and appended to the fact log, which is
the system of record. What an attestation MEANS is
:mod:`axor_sentinel.sentinel.attestation`'s — that it is append-only, that a
revocation is itself an event rather than a deletion, that a revocation counts
only from the keyset that made the attestation, that a reason and an operator
identity are required — and it is imported from there, not restated here.

Restating it is what went wrong. This surface stored ``revokes`` on every
attestation, handed it to the UI, and never applied it: a revoked attestation
kept covering its branch for good, and the UI printed "revokes" beside a
coverage that had not changed. Sentinel had the rule the whole time.

An attestation vouches two ways, and they are different contracts, so they are
different fields:

* ``covers`` names FACT IDS. That is the kernel's contract
  (:class:`axor_core.kernel.events.Fact`), and covering a fact discharges it
  from ``compute_level`` — the node's degradation drops. See
  :mod:`axor_backend.coverage`.
* ``causal_root`` names a value branch. That is Sentinel's contract
  (:class:`AttestationRecord.causal_root`), and it is what this module reads:
  who vouched for this branch, and does that still stand.

Scope is ``(run_id, causal_root)``. A value ref means something only inside the
run that minted it (see :mod:`axor_backend.provenance`), so an attestation
scoped to the bare ref covered the same ref in every other run — precisely the
operator-side reputation-laundering channel Sentinel's attestation module exists
in order not to have.

No heat is computed here. Sentinel's ``effective_score`` recomputes a branch's
suspicion over its attestations, but suspicion is Sentinel's number, produced
node-side and arriving on the plane as a ``heat_crossing`` fact. The plane
records who vouched for what and whether that still stands; it does not invent
a score to discharge.
"""
from __future__ import annotations

from typing import Any

from axor_sentinel.sentinel.attestation import (
    AttestationError,
    AttestationRecord,
    effective_revocations,
    validate,
)

ATTESTATION_FACT_TYPE = "operator_attestation"

__all__ = [
    "ATTESTATION_FACT_TYPE",
    "AttestationError",
    "covering",
    "record_from_fact",
    "validate_fact",
]


def record_from_fact(fact: dict[str, Any], ref: str = "") -> AttestationRecord:
    """One fact as Sentinel's record, scoped to the branch it vouches for.

    ``prior_heat`` is 0.0 and ``org`` empty on purpose: the plane has no heat to
    discharge, and its keyring is one operator keyset per deployment, so
    Sentinel's cross-keyset revocation check is the documented no-op rather than
    a second, invented notion of who may revoke.
    """
    return AttestationRecord(
        attestation_id=str(fact.get("fact_id") or ""),
        operator=str(fact.get("operator") or ""),
        reason=str(fact.get("reason") or ""),
        causal_root=ref or str(fact.get("causal_root") or ""),
        prior_heat=0.0,
        revokes=str(fact["revokes"]) if fact.get("revokes") else None,
    )


def validate_fact(fact: dict[str, Any]) -> None:
    """Sentinel's own admission rule, applied at the plane's front door.

    Raises :class:`AttestationError` — a missing reason or a missing operator
    identity. Both are Sentinel's requirements (decision 8), and an attestation
    the plane accepts but Sentinel would refuse is a fact whose meaning the two
    sides disagree about.
    """
    validate(record_from_fact(fact, ""))


def covering(facts: list[dict[str, Any]], ref: str) -> list[dict[str, Any]]:
    """The attestation history of one branch, newest first.

    ``facts`` are this run's attestation facts, oldest first (as the store
    returns them), and the branch is matched on ``causal_root``. Nothing is
    filtered out — history runs in both directions, so a revoked attestation and
    the revocation that ended it both stay — but each entry says whether its
    coverage is still in effect.

    A revocation is matched by the ``fact_id`` it names, not by its own
    ``causal_root``: a revocation that forgot to repeat the branch would
    otherwise be a silent no-op, recorded and ignored.
    """
    attesting = {
        str(f.get("fact_id") or ""): f
        for f in facts
        if f.get("fact_type") == ATTESTATION_FACT_TYPE
        and not f.get("revokes")
        and str(f.get("causal_root") or "") == ref
    }
    relevant = [
        f for f in facts
        if f.get("fact_type") == ATTESTATION_FACT_TYPE
        and (
            str(f.get("fact_id") or "") in attesting
            or (f.get("revokes") and str(f["revokes"]) in attesting)
        )
    ]
    revoked = effective_revocations(
        [record_from_fact(f, ref) for f in relevant]
    )
    return [
        {
            "fact_id": str(f.get("fact_id") or ""),
            "operator": str(f.get("operator") or ""),
            "reason": str(f.get("reason") or ""),
            "covers": [str(c) for c in (f.get("covers") or ())],
            "revokes": str(f["revokes"]) if f.get("revokes") else None,
            "in_effect": (
                False if f.get("revokes")
                else str(f.get("fact_id") or "") not in revoked
            ),
        }
        for f in reversed(relevant)
    ]
