"""The platform's record-driven path, against the kernel's taint-floor vectors.

`axor_core/vectors/taint_floor.json` is the ecosystem's statement of what the
taint floor decides, and it is read from the installed kernel — there is no copy
of it here. A conformance file mirrored into the product it checks is not a
shared statement; it is a second opinion waiting to drift.

What this proves is NOT the gate. The gate is imported, and axor-core proves it
against these same vectors. What this proves is the DECODE: that a recorded
event, read back through `axor_core.policy.from_record`, reaches the gate
carrying what it was decided on. That is the half this platform owns, and it is
the half that was wrong — the export converter used to reimplement the gate
rather than decode into it.
"""
from __future__ import annotations

import pytest
from axor_core.policy.from_record import (
    DECISIVE_NORMALIZED_FIELDS,
    IncompleteRecord,
    causal_root_from_record,
    normalized_from_record,
)
from axor_core.policy.gates import taint_gate
from axor_core.vectors import TAINT_FLOOR

CASES = TAINT_FLOOR()["vectors"]


@pytest.mark.parametrize("vec", CASES, ids=[v["name"] for v in CASES])
def test_a_recorded_event_decodes_into_the_verdict_it_was_gated_on(vec: dict) -> None:
    decision = taint_gate(
        vec["tool"],
        normalized_from_record(vec["tool"], vec["normalized"]),
        causal_root_from_record(vec["root"]),
        floor_active=bool(vec.get("floor_active")),
        egress_sinks=frozenset(vec.get("egress_sinks") or ()),
        integrity_superseded=bool(vec.get("integrity_superseded")),
    )
    if vec["expect"] == "allow":
        assert decision is None, f"expected ALLOW, got DENY: {decision}"
        return
    assert decision is not None, "expected DENY, the gate allowed"
    assert decision.category == vec["category"]
    assert vec["axis"] in decision.reason


@pytest.mark.parametrize("dropped", DECISIVE_NORMALIZED_FIELDS)
def test_a_partial_normalized_block_is_refused_not_defaulted(dropped: str) -> None:
    """Absent is not False. Each of these can turn a recorded DENY into a
    recomputed ALLOW, so a record missing one cannot be judged at all — the
    export refuses the run rather than exporting a verdict it guessed."""
    full = {f: False for f in DECISIVE_NORMALIZED_FIELDS} | {"destination_kind": "none"}
    partial = {k: v for k, v in full.items() if k != dropped}
    with pytest.raises(IncompleteRecord) as exc:
        normalized_from_record("send_email", partial, where="n:1")
    assert dropped in str(exc.value)
    assert "n:1" in str(exc.value)


def test_an_unknown_taint_source_still_taints() -> None:
    """Over-tainting is the safe direction; dropping a source we cannot name
    would turn a tainted value trusted."""
    assert causal_root_from_record({"sources": ["a-source-from-the-future"]}).is_tainted
