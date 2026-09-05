"""The platform's record-driven path, against the shared taint-floor vectors.

`test-vectors/taint-floor.json` is the repo's existing home for a cross-product
conformance file (next to `jcs-signing.json`, which the adapter and the plane
service both verify). The canonical copy ships inside axor-core as
`axor_core/vectors/taint_floor.json`; this platform pins axor-core from PyPI, so
until a release carries it the file is mirrored here and
`test_the_mirrored_vectors_match_the_kernels` compares the two whenever the
kernel does have it.

What this proves is NOT the gate — the gate is imported, and axor-core proves it
against the same vectors. What this proves is the DECODE: that a recorded event,
read back by `kernel_record`, reaches the kernel carrying what it was gated on.
That is the half this platform owns, and it is the half that was wrong: the
export converter used to reimplement the gate rather than decode into it.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from axor_backend.kernel_record import (
    DECISIVE_NORMALIZED_FIELDS,
    IncompleteRecord,
    causal_root_from_record,
    normalized_from_record,
)
from axor_core.policy.gates import taint_gate

VECTORS_PATH = (
    pathlib.Path(__file__).resolve().parents[3] / "test-vectors" / "taint-floor.json"
)
DOC = json.loads(VECTORS_PATH.read_text("utf-8"))
CASES = DOC["vectors"]


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
    """Absent is not False. Each of these fields can turn a recorded DENY into a
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
    root = causal_root_from_record({"sources": ["a-source-from-the-future"]})
    assert root.is_tainted


def test_the_mirrored_vectors_match_the_kernels() -> None:
    """The canonical copy lives in axor-core. When the installed kernel carries
    it, the mirror here must be identical — a conformance file that has drifted
    from the thing it conforms to is worse than none."""
    try:
        from axor_core.vectors import TAINT_FLOOR
    except ImportError:
        pytest.skip("installed axor-core predates the shipped vectors")
    assert TAINT_FLOOR() == DOC, (
        "test-vectors/taint-floor.json has drifted from axor_core's copy; "
        "the kernel's is canonical"
    )
