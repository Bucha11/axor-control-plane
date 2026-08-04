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
