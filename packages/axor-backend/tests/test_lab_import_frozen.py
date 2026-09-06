"""The Lab→CP seam, checked on EVERY run — no axor-lab installed.

`test_lab_import.py` drives the real Lab pipeline, which needs `lab_contracts`
and `lab_runner`. axor-lab is not published to PyPI and this workspace resolves
ecosystem deps from PyPI only (plan decision 5.1 retired git refs), so those
`importorskip` calls skip the whole module — in CI, always.

That is how the seam rotted without anyone noticing. The receiving side here is
complete: it converts a Lab `trace/v1` into kernel events, folds them through
axor-core's replay, and marks a pin `replayable` only when the pinned verdict
actually reproduces. It reads the trace bodies from `regression_traces`. axor-lab
never wrote that key, so every pin it ever exported landed
`skipped: "package carries no trace body for this pin"` — a fully-built consumer
with no producer, and a module that asserts otherwise but never runs.

So the fixture is FROZEN: real bytes from a real `axor-lab export-cp`, checked
in. It exercises the load-bearing claim on every CI run, with no optional
dependency to skip on. The generative tests next door stay for a developer who
does have axor-lab installed.

What this cannot catch on its own is axor-lab regressing and stopping producing
`regression_traces` again — a frozen good package stays good. The producer side
is guarded there, by `tests/test_cp_export_carries_trace_bodies.py`, which
asserts an export embeds a body for every pin it carries. The two together cover
both ends of the seam; either alone leaves the half nobody runs.

To regenerate after an intentional change to the export shape, from an axor-lab
checkout: build a two-arm bundle on the REAL kernel (`real_kernel_version()`),
pin one governed DENY trace and one governed ALLOW trace, and dump
`export_cp(bundle, regressions=pins, traces=traces).config` here. Both sides
matter: a fixture of denials only would not catch a kernel that denies
everything.
"""

from __future__ import annotations

import copy
import json
import pathlib
from typing import Any

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.lab_import import (
    deploy_plans,
    package_id_of,
    validate_cp_deploy,
)

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "lab-cp-deploy.json"


@pytest.fixture(scope="module")
def package() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


class TestTheFrozenPackageIsWhatLabProduces:
    def test_it_validates(self, package: dict[str, Any]) -> None:
        assert validate_cp_deploy(package) == []

    def test_it_is_a_finalized_evidence_backed_export(self, package: dict[str, Any]) -> None:
        """A template dump carries no evidence and is refused; this is the real
        thing, so it must declare itself verified."""
        assert package["schema_version"] == "axor-cp-deploy/v1"
        assert package["verified"] is True

    def test_it_was_measured_on_the_real_kernel(self, package: dict[str, Any]) -> None:
        """A pin recorded under Lab's in-process reference kernel converts but is
        never replayed here — this CP runs the real axor-core."""
        assert str(package["kernel"]).startswith("axor-core@")

    def test_every_pin_carries_its_trace_body(self, package: dict[str, Any]) -> None:
        """The key axor-lab did not write. Without it `deploy_plans` has nothing
        to convert and every pin stays skipped."""
        bodies = package["regression_traces"]
        assert {str(p["trace_id"]) for p in package["regressions"]} == set(bodies)

    def test_each_body_names_its_own_trace_id(self, package: dict[str, Any]) -> None:
        for trace_id, body in package["regression_traces"].items():
            assert str(body["trace_id"]) == str(trace_id)


class TestThePinsAreReplayableHere:
    def test_both_sides_of_the_corpus_are_covered(self, package: dict[str, Any]) -> None:
        """A must_block (attack → DENY) and a must_pass (faithful → ALLOW): a
        fixture with only denials would not catch a kernel that denies
        everything."""
        plans = deploy_plans(package, package_id_of(package))
        assert {p.side for p in plans} == {"must_block", "must_pass"}

    def test_every_pin_replays_and_reproduces_its_verdict(
        self, package: dict[str, Any],
    ) -> None:
        """The whole claim. `replayable` is only True when the carried body
        content-hashes to the pin's ref, converts to kernel events, AND
        re-gating those events under a config compiled from the package's own
        manifests reproduces the pinned verdict."""
        for plan in deploy_plans(package, package_id_of(package)):
            assert plan.replayable, f"{plan.trace_id}: {plan.reason}"
            assert plan.event_lines, f"{plan.trace_id} converted to no events"

    def test_a_package_without_bodies_degrades_honestly(
        self, package: dict[str, Any],
    ) -> None:
        """Backward compatibility, and the exact state every real export was in:
        the pins are still created, just not replayable, with a reason that says
        why rather than a silent pass."""
        stripped = {**package, "regression_traces": {}}
        plans = deploy_plans(stripped, package_id_of(stripped))
        assert plans, "the pins themselves must still be created"
        assert not any(p.replayable for p in plans)
        assert all("no trace body" in str(p.reason) for p in plans)


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={}, allow_unsigned=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        yield c


class TestTheDeployRouteAcceptsIt:
    async def test_it_is_accepted_and_its_pins_become_replayable_corpus(
        self, client: httpx.AsyncClient, package: dict[str, Any],
    ) -> None:
        response = await client.post("/v1/lab/deploy", json=package)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pins_created"] == len(package["regressions"])
        assert body["pins_replayable"] == len(package["regressions"]), body["pins_skipped"]
        assert body["policy_stored"] is True

    async def test_a_tampered_package_is_refused(
        self, client: httpx.AsyncClient, package: dict[str, Any],
    ) -> None:
        tampered = {**package, "config_hash": "sha256:" + "0" * 64}
        response = await client.post("/v1/lab/deploy", json=tampered)
        assert response.status_code == 422, response.text


# ── the corpus key must survive the column it is stored in ────────────────────

class TestAPinIdCannotCollideWithAnother:
    """`lab:{trace_id}` goes into a 64-character key and used to be TRUNCATED.

    Two pins agreeing on their first 60 characters became one row, the second
    overwriting the first — so a package pinning both sides of a case could land
    only the must_pass side, in the corpus whose entire premise (decision 11) is
    that a config blocking everything must not pass. The route reported both as
    created. These are refusals now, with the reason, before anything is stored.
    """

    KERNEL = "axor-core@0.10.2"
    POLICY: dict = {"profile": "default"}

    def _package(self, regressions: list[dict]) -> dict:
        from axor_backend.lab_export import condition_config_hash

        return {
            "schema_version": "axor-cp-deploy/v1", "verified": True,
            "kernel": self.KERNEL, "policy": self.POLICY,
            "config_hash": condition_config_hash(self.KERNEL, self.POLICY),
            "parametric_config_hash": "pch",
            "tool_manifests": [{
                "schema_version": "tool-manifest/v1", "id": "post",
                "args_schema": {}, "side_effecting": True,
                "effect": {"default_class": "EXPORT", "driving_args": ["b"]},
            }],
            "regressions": regressions,
            "source": {"bundle_id": "b1", "condition_id": "c1"},
        }

    @staticmethod
    def _pin(trace_id: str, verdict: str = "DENY") -> dict:
        return {"trace_id": trace_id, "expected_verdict": verdict,
                "trace_ref": "sha256:x", "expected_sequence": [verdict]}

    def test_two_pins_differing_past_the_cut_are_refused(self) -> None:
        from axor_backend.lab_import import validate_cp_deploy

        reasons = validate_cp_deploy(self._package([
            self._pin("t" * 60 + "_alpha", "DENY"),
            self._pin("t" * 60 + "_bravo", "ALLOW"),
        ]))
        assert any("characters" in r for r in reasons), reasons

    def test_an_id_that_exactly_fits_is_accepted(self) -> None:
        """The bound is the column minus the `lab:` prefix — not a round number
        someone guessed."""
        from axor_backend.lab_import import validate_cp_deploy

        assert validate_cp_deploy(self._package([self._pin("t" * 60)])) == []
        assert validate_cp_deploy(self._package([self._pin("t" * 61)])) != []

    def test_a_duplicate_trace_id_in_one_package_is_refused(self) -> None:
        """Two pins on one id resolve to a single corpus row, and which side
        survives depends on array order."""
        from axor_backend.lab_import import validate_cp_deploy

        reasons = validate_cp_deploy(self._package([
            self._pin("same", "DENY"), self._pin("same", "ALLOW"),
        ]))
        assert any("duplicate trace_id" in r for r in reasons), reasons

    def test_a_package_cannot_carry_unbounded_pins(self) -> None:
        """Every pin is content-hashed, converted to kernel events and REPLAYED
        before the request answers, so the count is caller-chosen work. 5000 of
        them took 15 seconds in one request and were accepted."""
        from axor_backend.lab_import import MAX_PINS_PER_PACKAGE, validate_cp_deploy

        over = [self._pin(f"tr{i}") for i in range(MAX_PINS_PER_PACKAGE + 1)]
        reasons = validate_cp_deploy(self._package(over))
        assert any("ceiling" in r for r in reasons), reasons
        assert validate_cp_deploy(self._package(over[:MAX_PINS_PER_PACKAGE])) == []


# ── the manifests must still be validated, now that we no longer do it here ───

class TestABrokenManifestIsStillRefused:
    """`validate_cp_deploy` used to restate `tool-manifest/v1` by hand — the
    required fields, the effect-class enum, the id/args_schema/side_effecting
    types — forty lines duplicating a schema that lives in the Lab and, now,
    in axor-core. That restatement is gone; the deploy schema `$ref`s the
    manifest schema instead.

    The only test that covered this refusal lives in `test_lab_import.py`,
    which `importorskip`s axor-lab and therefore never runs in CI. Deleting
    the checks without moving the test here would have removed the guarantee
    along with the duplication.
    """

    def _package(self, manifests: list[dict]) -> dict:
        p = TestAPinIdCannotCollideWithAnother()._package([])
        return {**p, "tool_manifests": manifests}

    def test_a_manifest_missing_its_required_fields_is_refused(self) -> None:
        from axor_backend.lab_import import validate_cp_deploy

        reasons = validate_cp_deploy(self._package([{"id": "x"}]))
        assert reasons and all("tool_manifests[0]" in r for r in reasons), reasons

    def test_an_effect_class_outside_the_four_is_refused(self) -> None:
        """READ / WRITE / EXPORT / EXEC is the kernel's vocabulary. A fifth
        would compile into a config the gate cannot reason about."""
        from axor_backend.lab_import import validate_cp_deploy

        bad = {"schema_version": "tool-manifest/v1", "id": "post",
               "args_schema": {}, "side_effecting": True,
               "effect": {"default_class": "TRANSMOGRIFY", "driving_args": []}}
        assert validate_cp_deploy(self._package([bad])) != []

    def test_two_manifests_sharing_an_id_are_refused(self) -> None:
        """The schema cannot express this and so it stays here: the manifests
        are compiled into a config keyed by tool id, so the second silently
        replaces the first — with a different effect class, different driving
        args, and different sensitive fields."""
        from axor_backend.lab_import import validate_cp_deploy

        one = {"schema_version": "tool-manifest/v1", "id": "post",
               "args_schema": {}, "side_effecting": True,
               "effect": {"default_class": "EXPORT", "driving_args": ["b"]}}
        two = {**one, "effect": {"default_class": "READ", "driving_args": []}}
        reasons = validate_cp_deploy(self._package([one, two]))
        assert any("duplicate tool id" in r for r in reasons), reasons

    def test_no_manifests_at_all_is_refused(self) -> None:
        """Evidence produced against no tools is not evidence."""
        from axor_backend.lab_import import validate_cp_deploy

        assert validate_cp_deploy(self._package([])) != []


class TestACarriedTraceMustBeFiledUnderItsOwnId:
    """`regression_traces` is the map the CP replays pins from. A body stored
    under someone else's key would be content-hashed against, and replayed as
    evidence for, the wrong pin. The schema requires each body to HAVE a
    trace_id; only a reader holding the key as well can check they agree."""

    def test_a_body_under_the_wrong_key_is_refused(self) -> None:
        from axor_backend.lab_import import validate_cp_deploy

        base = TestAPinIdCannotCollideWithAnother()
        package = {**base._package([base._pin("t1")]),
                   "regression_traces": {"t1": {"trace_id": "t9"}}}
        reasons = validate_cp_deploy(package)
        assert any("does not match its key" in r for r in reasons), reasons


class TestAPinnedVerdictMustAgreeWithItsSequence:
    """`expected_verdict` summarizes `expected_sequence`; both are pinned so a
    multi-call trace cannot match on its final verdict alone. A pin whose two
    halves disagree pins nothing coherent, and the schema — which validates each
    field on its own — cannot see it."""

    def test_a_verdict_contradicting_the_final_recorded_one_is_refused(self) -> None:
        from axor_backend.lab_import import validate_cp_deploy

        base = TestAPinIdCannotCollideWithAnother()
        package = base._package([{
            "trace_id": "t1", "expected_verdict": "ALLOW",
            "trace_ref": "sha256:x", "expected_sequence": ["ALLOW", "DENY"],
        }])
        reasons = validate_cp_deploy(package)
        assert any("contradicts" in r for r in reasons), reasons


# ── the config a pin is replayed under is the one it ran under ────────────────

class TestTheReplayConfigIsCarriedNotRecompiled:
    """`lab_trace` used to compile a governor config from the tool manifests.

    Its compiler and the Lab's disagreed on every point that mattered. On the
    same three manifests the Lab produced egress sinks ['send_email'] and the CP
    produced ['write_file']: the CP read `side_effecting` as egress, never looked
    at `effect.resolve`, and dropped the allowlist value-policies entirely — it
    was never even passed the policy they come from. Every pin whose verdict
    turned on any of that was filed `skipped` with a reason blaming the trace.

    The package carries the config now, and it is checked against the hash
    recorded beside it.
    """

    @staticmethod
    def _plans(package: dict[str, Any]) -> list:
        return deploy_plans(package, package_id_of(package))

    def test_a_pin_without_its_scenarios_config_is_not_replayed(
        self, package: dict[str, Any],
    ) -> None:
        """Not silently, and not by compiling a substitute."""
        stripped = {**package, "runtime_configs": {}}
        plans = self._plans(stripped)
        assert plans and not any(p.replayable for p in plans)
        assert all("no runtime config" in str(p.reason) for p in plans), [
            p.reason for p in plans
        ]

    def test_a_config_that_is_not_the_one_recorded_is_refused(
        self, package: dict[str, Any],
    ) -> None:
        """The hash travelled with the package long before the config did. A body
        that does not match it is not what ran, whoever swapped it."""
        tampered = {
            **package,
            "runtime_configs": {
                sid: {**cfg, "egress_sinks": []}
                for sid, cfg in package["runtime_configs"].items()
            },
        }
        plans = self._plans(tampered)
        assert plans and not any(p.replayable for p in plans)
        assert all("does not match its recorded hash" in str(p.reason)
                   for p in plans), [p.reason for p in plans]

    def test_the_carried_config_is_what_actually_gates_the_replay(
        self, package: dict[str, Any],
    ) -> None:
        """Drop the egress sink and fix the hash to match: nothing is tampered,
        the package is internally consistent — and the DENY pin can no longer
        reproduce, because under that config nothing denies. This is what the
        recompiled config was silently getting wrong."""
        from axor_backend.lab_export import content_hash

        neutered = {sid: {**cfg, "egress_sinks": []}
                    for sid, cfg in package["runtime_configs"].items()}
        consistent = {
            **package,
            "runtime_configs": neutered,
            "runtime_config_hashes": {sid: content_hash(cfg)
                                      for sid, cfg in neutered.items()},
        }
        plans = {p.side: p for p in self._plans(consistent)}
        assert not plans["must_block"].replayable
        assert "would not reproduce" in str(plans["must_block"].reason)


# ── a trace body is a trace, and its decisions belong to their own calls ──────

class TestAnEmbeddedBodyIsValidatedBeforeItIsConverted:
    """The bodies arrive inside an upload and went straight into the converter,
    which read whatever it was handed. Now that axor-core owns `trace/v1`, the
    consumer of a trace can check it is one."""

    def test_a_body_that_is_not_a_trace_is_refused(
        self, package: dict[str, Any],
    ) -> None:
        broken = {**package, "regression_traces": {
            tid: {"trace_id": tid} for tid in package["regression_traces"]
        }}
        plans = deploy_plans(broken, package_id_of(broken))
        # the ref check fires first for a body this different; either refusal is
        # honest, but none of them may be replayed
        assert plans and not any(p.replayable for p in plans)

    def test_a_binding_naming_a_value_the_ledger_lacks_is_refused(
        self, package: dict[str, Any],
    ) -> None:
        """It used to be skipped over, and the call replayed with LESS taint than
        the trace recorded — the one direction of error that turns a DENY into a
        PASS."""
        from axor_backend.lab_trace import TraceNotConvertible, lab_trace_to_events

        body = copy.deepcopy(next(iter(package["regression_traces"].values())))
        for event in body["events"]:
            if event.get("type") == "tool_call_intent":
                event["arg_bindings"] = {"recipient": "v_not_in_the_ledger"}
        with pytest.raises(TraceNotConvertible, match="absent from the ledger"):
            lab_trace_to_events(body)

    def test_two_decisions_on_one_call_id_are_refused(
        self, package: dict[str, Any],
    ) -> None:
        """`call_id` exists, per its own definition, so a replay can detect a
        missing or duplicated decision. A dict comprehension detected neither:
        the second silently replaced the first."""
        from axor_backend.lab_trace import TraceNotConvertible, lab_trace_to_events

        body = copy.deepcopy(next(iter(package["regression_traces"].values())))
        decision = next(e for e in body["events"] if e["type"] == "gate_decision")
        body["events"].append({**decision, "seq": 99,
                               "decision": {**decision["decision"],
                                            "verdict": "ALLOW"}})
        with pytest.raises(TraceNotConvertible, match="share call_id"):
            lab_trace_to_events(body)

    def test_a_decision_with_no_call_id_is_refused(
        self, package: dict[str, Any],
    ) -> None:
        """`call_id` is OPTIONAL in trace/v1. Every event without one used to
        collapse into the single key "None", so in a two-call trace BOTH calls
        took the last decision recorded and an ALLOW came back carrying someone
        else's DENY."""
        from axor_backend.lab_trace import TraceNotConvertible, lab_trace_to_events

        body = copy.deepcopy(next(iter(package["regression_traces"].values())))
        for event in body["events"]:
            event.pop("call_id", None)
        with pytest.raises(TraceNotConvertible, match="no call_id"):
            lab_trace_to_events(body)

    def test_a_call_with_no_recorded_decision_is_refused(
        self, package: dict[str, Any],
    ) -> None:
        from axor_backend.lab_trace import TraceNotConvertible, lab_trace_to_events

        body = copy.deepcopy(next(iter(package["regression_traces"].values())))
        body["events"] = [e for e in body["events"] if e["type"] != "gate_decision"]
        with pytest.raises(TraceNotConvertible, match="no gate_decision"):
            lab_trace_to_events(body)


class TestThePinnedSequenceIsWhatMustReproduce:
    """The pin carries the whole ordered verdict sequence so a multi-call trace
    cannot match on its final verdict alone. The CP read it once, checked it
    against `expected_verdict`, and never used it again — reproduction compared
    only the last TOOL_CALL."""

    def test_a_pin_claiming_more_calls_than_the_trace_has_is_not_replayed(
        self, package: dict[str, Any],
    ) -> None:
        longer = copy.deepcopy(package)
        for pin in longer["regressions"]:
            pin["expected_sequence"] = [*pin["expected_sequence"],
                                        pin["expected_verdict"]]
        plans = deploy_plans(longer, package_id_of(longer))
        assert plans and not any(p.replayable for p in plans)
        assert all("would not reproduce" in str(p.reason) for p in plans)
