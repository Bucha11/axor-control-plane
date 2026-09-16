"""What the exported incident package CLAIMS about provenance.

Separate from `test_lab_export.py` on purpose: that module needs axor-lab
installed and `importorskip`s the whole file, which in CI means it never runs —
the exact mechanism that let the other half of this seam rot unnoticed
(see test_lab_import_frozen.py). Nothing here needs the Lab. These assertions
are about the package this platform PRODUCES, against the trace/v1 rules it is
written to, so they run on every pass.

An incident package is evidence. Its value ledger states where untrusted content
entered a run, and an investigator reads it to find the tool to fix. A ledger
that is structurally valid and points at the wrong tool is worse than one that
fails to build.
"""
from __future__ import annotations

import pytest


def _events(*calls: dict) -> list[dict]:
    return list(calls)


def _norm(**over: object) -> dict:
    return {
        "operation": "other", "target_kind": "workdir", "destination_kind": "none",
        "provenance": "unknown", "reads_secret_like_data": False,
        "writes_outside_workdir": False, "executes_generated_code": False,
        "after_external_read": False, "after_secret_access": False,
        "data_flow": "none", **over,
    }


def _ev(seq: int, kind: str, verdict: str | None, **payload: object) -> dict:
    return {"schema_version": "1.0", "seq": seq, "node_id": "n", "kind": kind,
            "ts": "t", "causal_root": None, "gate": None, "verdict": verdict,
            "payload": payload}


def test_a_derived_value_names_where_the_taint_ENTERED() -> None:
    """trace/v1: `sources` is "the transitive causal_root", `derived_from` is
    "the immediate edge".

    The export named the value's own producing tool as an `external_read`, so a
    summary of an injected email claimed the injection entered at the SUMMARIZER.
    An incident package is evidence; pointing an investigator at the tool that
    merely carried the taint, rather than the one that let it in, is the kind of
    wrong that survives review because it looks structurally fine.
    """
    from axor_backend.lab_export import build_incident_package

    pkg = build_incident_package(_events(
        _ev(0, "tool_call", "pass", tool="email_read", args={}, arg_refs={},
            normalized=_norm(), driving_root={"sources": [], "sensitive": False},
            floor_active=False),
        _ev(1, "tool_result", None, tool="email_read", value_ref="v_mail",
            root={"sources": ["web"], "sensitive": False}),
        _ev(2, "tool_call", "pass", tool="summarize", args={"text": "x"},
            arg_refs={"text": "v_mail"}, driving_args=["text"], normalized=_norm(),
            driving_root={"sources": ["web"], "sensitive": False}, floor_active=False),
        _ev(3, "tool_result", None, tool="summarize", value_ref="v_sum",
            root={"sources": ["web"], "sensitive": False}),
        _ev(4, "tool_call", "deny", tool="slack_post", args={"text": "x"},
            arg_refs={"text": "v_sum"}, driving_args=["text"],
            normalized=_norm(destination_kind="external_domain"),
            driving_root={"sources": ["web"], "sensitive": False},
            floor_active=False, roles={"egress_sink": True}),
    ), {"run_id": "r", "scenario": "s"})

    rows = {row["value_id"]: row for row in pkg["trace"]["values"]}
    assert rows["v_sum"]["sources"] == [
        {"kind": "external_read", "origin_ref": "tool_result:email_read"}
    ], "the causal root is the tool the taint entered through, not the deriving one"
    assert rows["v_sum"]["derived_from"] == ["v_mail"], "the immediate edge stays"
    # A tool is not an LLM: `model_extraction` is reserved for a value the model
    # composed, and the enum has no entry for "a tool computed it".
    assert "transformations" not in rows["v_sum"]


def test_a_model_composed_value_has_a_traceable_parent() -> None:
    """An argument the model produced from its context has no value ref and never
    will — the kernel mints refs only for tool output. It used to be minted with
    no parent at all, so the ledger emitted the dangling `tool_result:` as its
    source. trace/v1 says such a value's `derived_from` is ALL untrusted context
    values live at the call, conservatively."""
    from axor_backend.lab_export import build_incident_package

    pkg = build_incident_package(_events(
        _ev(0, "tool_call", "pass", tool="web_read", args={}, arg_refs={},
            normalized=_norm(), driving_root={"sources": [], "sensitive": False},
            floor_active=False),
        _ev(1, "tool_result", None, tool="web_read", value_ref="v_web",
            root={"sources": ["web"], "sensitive": False}),
        _ev(2, "tool_call", "deny", tool="post", args={"body": "composed"},
            arg_refs={}, driving_args=["body"],
            normalized=_norm(destination_kind="external_domain"),
            arg_provenance={"body": {"sources": ["web"], "sensitive": False}},
            driving_root={"sources": ["web"], "sensitive": False},
            floor_active=False, roles={"egress_sink": True}),
    ), {"run_id": "r", "scenario": "s"})

    composed = next(r for r in pkg["trace"]["values"] if r["value_id"].startswith("m_"))
    assert composed["sources"] == [
        {"kind": "external_read", "origin_ref": "tool_result:web_read"}
    ], "no dangling `tool_result:` — the taint is traced to the read that rooted it"
    assert composed["derived_from"] == ["v_web"]
    assert composed["transformations"] == ["model_extraction"]


def test_an_egress_sink_without_a_driving_arg_is_refused_in_the_reason_list() -> None:
    """Two checks disagreed: the batch tested `egress`, scenario synthesis tested
    `egress AND driving_args`. So a run was refused for "no egress consequence",
    the operator added a sink, and it was refused again — by a different message,
    raised alone, after the reason list had been reported. Every reason, in one
    list, is this module's contract with its caller."""
    from axor_backend.lab_export import LabExportError, build_incident_package

    with pytest.raises(LabExportError) as exc:
        build_incident_package(_events(
            _ev(0, "tool_call", "pass", tool="web_read", args={}, arg_refs={},
                normalized=_norm(), driving_root={"sources": [], "sensitive": False},
                floor_active=False),
            _ev(1, "tool_result", None, tool="web_read", value_ref="v_web",
                root={"sources": ["web"], "sensitive": False}),
            _ev(2, "tool_call", "deny", tool="post", args={}, arg_refs={},
                driving_args=[], normalized=_norm(destination_kind="external_domain"),
                driving_root={"sources": ["web"], "sensitive": False},
                floor_active=False, roles={"egress_sink": True}),
        ), {"run_id": "r", "scenario": "s"})

    reasons = " ".join(exc.value.reasons)
    assert "driving argument" in reasons
    assert "internal:" not in reasons, "the batch must catch this, not the synthesis"
