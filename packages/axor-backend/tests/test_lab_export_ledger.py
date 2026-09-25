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


# ── integrity sinks (axor-core `integrity_sinks`) ─────────────────────────────

def _integrity_run(verdict: str) -> list[dict]:
    """A run that read an injection, then called an operator-declared integrity
    sink (a password update) — plus an egress call, so the run has a scenario sink."""
    return _events(
        _ev(0, "tool_call", "pass", tool="read_file", args={}, arg_refs={},
            normalized=_norm(), driving_root={"sources": [], "sensitive": False},
            floor_active=False),
        _ev(1, "tool_result", None, tool="read_file", value_ref="v_file",
            root={"sources": ["web"], "sensitive": False}),
        _ev(2, "tool_call", verdict, tool="update_password", args={"password": "x"},
            arg_refs={}, driving_args=["password"], normalized=_norm(),
            arg_provenance={"password": {"sources": ["web"], "sensitive": False}},
            driving_root={"sources": ["web"] if verdict == "deny" else [],
                          "sensitive": False},
            floor_active=False, roles={"integrity_sink": True}),
        _ev(3, "tool_call", "deny", tool="send_money", args={"recipient": "x"},
            arg_refs={"recipient": "v_file"}, driving_args=["recipient"],
            normalized=_norm(destination_kind="external_domain"),
            driving_root={"sources": ["web"], "sensitive": False},
            floor_active=False, roles={"egress_sink": True}),
    )


def test_an_integrity_sink_deny_is_re_decided_with_the_role_and_exports() -> None:
    """The recompute must pass the recorded role to the kernel's taint_gate: an
    integrity sink is none of egress / outside-workspace write / exec, so without
    the role the recorded DENY re-decides as an ALLOW and the run is refused for a
    mismatch the converter manufactured. With it, the DENY reproduces and the run
    exports — Lab's manifest compilation turns the WRITE into an integrity sink."""
    from axor_backend import lab_export

    assert lab_export._GATE_TAKES_INTEGRITY_SINKS, "needs an axor-core with integrity_sinks"
    pkg = lab_export.build_incident_package(_integrity_run("deny"),
                                            {"run_id": "r", "scenario": "s"})
    decision = next(e["decision"] for e in pkg["trace"]["events"]
                    if e.get("type") == "gate_decision" and e.get("tool") == "update_password")
    assert decision["verdict"] == "DENY"
    assert decision["driving_value_id"] == "m_n_2_password"


def test_an_allowed_integrity_sink_call_exports_as_a_write_tool() -> None:
    """The manifest states the role the schema can carry: WRITE with driving args."""
    from axor_backend.lab_export import build_incident_package

    pkg = build_incident_package(_integrity_run("pass"), {"run_id": "r", "scenario": "s"})
    manifest = next(m for m in pkg["manifests"] if m["id"] == "update_password")
    assert manifest["effect"] == {"default_class": "WRITE", "driving_args": ["password"]}
    assert manifest["side_effecting"] is True


def test_without_the_lab_compilation_a_deny_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """For a Lab whose compilation does not carry the role, the DENY would replay
    as an ALLOW — the export refuses it, naming why."""
    from axor_backend import lab_export
    from axor_backend.lab_export import LabExportError

    monkeypatch.setattr(lab_export, "_LAB_COMPILES_INTEGRITY_SINKS", False)
    with pytest.raises(LabExportError) as exc:
        lab_export.build_incident_package(_integrity_run("deny"),
                                          {"run_id": "r", "scenario": "s"})
    reasons = " ".join(exc.value.reasons)
    assert "integrity sink 'update_password'" in reasons
    assert "would not reproduce" not in reasons
