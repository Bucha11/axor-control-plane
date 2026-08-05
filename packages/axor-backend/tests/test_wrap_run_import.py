"""The core/wrap → CP seam, checked on EVERY run — no axor-lab, no axor-wrap.

A runtime that governs with axor-core (through either wrapping path) pushes its
trace here as kernel-schema events, and `build_incident_package` turns a
recorded run back into a Lab incident. Every piece of that was built and none of
it worked, because the two ends were only ever compared by reading each other's
docstrings:

  - a refused tool call arrived as `EventKind.DENIAL` with its payload rebuilt
    as `{reason, intent_kind}`, and `_scan` has no trace/v1 representation for
    that kind → the whole run refused;
  - the `tool_result` branch was fed by an event class axor-core declares and
    never constructs, so no run had a source → "no untrusted source in the
    recorded run";
  - egress-ness was read only off the normalizer's `destination_kind`, which
    does not know a deployment's vocabulary (`send_email` normalises to `none`)
    → "no recorded egress consequence", on a run whose entire content was a
    blocked exfiltration.

`test_lab_export.py` covers this module's output against the real Lab, and is
`importorskip`-ed on axor-lab — which is not on PyPI, so in CI it never runs.
This file has no optional dependency: the fixture is FROZEN kernel events from a
real `ToolCallGovernor` session bridged by `axor_wrap.plane.bridge`, so the seam
is exercised on every run.

To regenerate after an intentional change to the event shape: drive a governor
with an untrusted source, one clean egress and one tainted egress, and dump
`[asdict(e) for e in trace_to_kernel(gov.trace_events)]`. Both egress sides
matter — a fixture of denials only would not catch a kernel that denies
everything.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
from axor_backend.lab_export import LabExportError, build_incident_package

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "wrap-kernel-events.json"


@pytest.fixture(scope="module")
def recorded() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def package(recorded: dict[str, Any]) -> dict[str, Any]:
    return build_incident_package(recorded["events"], recorded["run_meta"])


class TestTheRecordedRunHasWhatAnIncidentNeeds:
    def test_the_refused_call_is_a_tool_call_with_a_deny_verdict(
        self, recorded: dict[str, Any],
    ) -> None:
        """Not `denial`. `_scan` only re-gates `tool_call`, so a denial in any
        other kind is a call the converter cannot represent at all."""
        denials = [
            e for e in recorded["events"]
            if e["kind"] == "tool_call" and e["verdict"] == "deny"
        ]
        assert len(denials) == 1
        assert denials[0]["gate"] == "taint_floor"

    def test_the_run_carries_a_source_event(self, recorded: dict[str, Any]) -> None:
        """The `tool_result` that says where taint entered. There were none —
        the bridge branch that produces them was fed by an event class nothing
        constructed."""
        results = [e for e in recorded["events"] if e["kind"] == "tool_result"]
        assert len(results) == 1
        assert results[0]["payload"]["root"]["sources"] == ["web"]
        assert results[0]["causal_root"] == results[0]["payload"]["value_ref"]

    def test_the_denied_argument_binds_to_that_source(
        self, recorded: dict[str, Any],
    ) -> None:
        source = next(e for e in recorded["events"] if e["kind"] == "tool_result")
        denial = next(
            e for e in recorded["events"]
            if e["kind"] == "tool_call" and e["verdict"] == "deny"
        )
        assert denial["payload"]["arg_refs"]["to"] == source["payload"]["value_ref"]

    def test_the_sink_declares_the_role_that_made_it_a_sink(
        self, recorded: dict[str, Any],
    ) -> None:
        """`send_email` normalises to `destination_kind: none` — the operator's
        declaration is the only record that it exports anything."""
        denial = next(
            e for e in recorded["events"]
            if e["kind"] == "tool_call" and e["verdict"] == "deny"
        )
        assert denial["payload"]["roles"]["egress_sink"] is True
        assert denial["payload"]["normalized"]["destination_kind"] == "none"


class TestItConvertsToAnIncidentPackage:
    def test_it_produces_a_package_at_all(self, package: dict[str, Any]) -> None:
        assert package["schema_version"] == "axor-lab-incident/v1"

    def test_both_tools_become_manifests_with_their_roles(
        self, package: dict[str, Any],
    ) -> None:
        by_id = {m["id"]: m for m in package["manifests"]}
        assert set(by_id) == {"read_inbox", "send_email"}
        assert by_id["read_inbox"]["untrusted_fields"] == ["result.content"]
        assert by_id["send_email"]["effect"]["default_class"] == "EXPORT"
        # the DECLARED driving arg, not "whichever argument happened to be
        # bound to a value ref"
        assert by_id["send_email"]["effect"]["driving_args"] == ["to"]

    def test_every_recorded_verdict_reproduces_under_label_based_replay(
        self, package: dict[str, Any],
    ) -> None:
        """`build_incident_package` refuses the run if any recorded verdict would
        not reproduce, so reaching a package at all IS this assertion — but the
        trace has to show both sides, or a kernel that denied everything would
        also pass."""
        verdicts = [
            (e["tool"], e["decision"]["verdict"])
            for e in package["trace"]["events"] if e["type"] == "gate_decision"
        ]
        assert verdicts == [
            ("read_inbox", "ALLOW"),
            ("send_email", "ALLOW"),   # the faithful send
            ("send_email", "DENY"),    # the exfiltration
        ]

    def test_the_violation_predicate_names_the_driving_argument(
        self, package: dict[str, Any],
    ) -> None:
        violation = package["scenario"]["violation"]
        assert violation["tool"] == "send_email"
        assert "prov(args.to)" in violation["where"]

    def test_the_injection_fixture_targets_the_source_tool(
        self, package: dict[str, Any],
    ) -> None:
        assert set(package["scenario"]["fixtures"]) == {"read_inbox"}


class TestItStillRefusesWhatItCannotRepresent:
    def test_a_run_with_no_source_is_refused(self, recorded: dict[str, Any]) -> None:
        """The exact state every bridged run used to be in. It must refuse with
        a reason, not silently export a scenario with no injection vector."""
        stripped = [e for e in recorded["events"] if e["kind"] != "tool_result"]
        with pytest.raises(LabExportError) as caught:
            build_incident_package(stripped, recorded["run_meta"])
        assert any("no untrusted source" in r for r in caught.value.reasons)

    def test_a_run_with_no_egress_role_and_no_destination_is_refused(
        self, recorded: dict[str, Any],
    ) -> None:
        """Dropping the declaration must not silently downgrade the sink to a
        read — it must lose the run, loudly."""
        blinded = [
            {**e, "payload": {**e["payload"], "roles": {}}}
            if e["kind"] == "tool_call" else e
            for e in recorded["events"]
        ]
        with pytest.raises(LabExportError) as caught:
            build_incident_package(blinded, recorded["run_meta"])
        assert any("no recorded egress consequence" in r for r in caught.value.reasons)
