"""Behavioral drift, graded — and the line it may not cross (ui-spec 8.2).

axor-probe projects a battery into an eval feed; axor-eval grades that feed into
a BEHAVIORAL_DRIFT EvidenceCase. Both ends shipped and neither had a caller:
measured across all seven repositories, `feed_audit` and
`BehavioralIntegrityAudit` appeared only in their own tests. A node posting
DRIFT_DETECTED with 3 escapes of 5 left one `probe_reports` row, a notification
carrying the bare verdict string, and nothing else:

    runs on this node  -> []
    regression corpus  -> {'must_block': 0, 'must_pass': 0, 'total': 0}

Neither library may import the other (P-34; axor-eval declares only axor-core).
This backend imports both, which makes it the only place the wire can exist —
and the only place a test can hold the two vocabularies against each other.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.drift_evidence import drift_case
from axor_eval.audit.behavioral_audit import BehavioralIntegrityAudit
from axor_eval.contracts import CORE_DEVIATIONS, DeviationType
from axor_probe.integration.eval import audit_payload
from axor_probe.signals.report import VERDICTS

NODE = "banking-assistant"


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={}, allow_unsigned=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        c._app = app  # type: ignore[attr-defined]
        yield c


def _battery(
    verdict: str = "DRIFT_DETECTED", escapes: int = 3, drift_score: float = 0.8,
    calibration: str = "UNCALIBRATED", session_id: str = "s1",
) -> dict:
    return {
        "session_id": session_id, "agent_id": NODE, "model": "m",
        "probe_library_version": "1.0.0", "overall_verdict": verdict,
        "families": [{"family": "refusal_drift",
                      "state": "escaped" if escapes else "clean",
                      "escapes": escapes, "probes": 5}],
        "probes_sent": 5, "probes_invalid": 0, "probes_triangulated": 0,
        "structural_failures": 0, "escape_count": escapes,
        "escape_rate": escapes / 5, "escape_rate_ci": [0.2, 0.88],
        "calibration_status": calibration,
        "max_drift_score_uncalibrated": drift_score,
    }


# ── the grade the plane used to throw away ────────────────────────────────────

async def test_a_drifting_battery_is_graded_on_the_health_surface(
    client: httpx.AsyncClient,
) -> None:
    await client.post(f"/v1/plane/{NODE}/probe-report", json=_battery())
    case = (await client.get(f"/v1/plane/{NODE}/probe-report")).json()["drift_case"]

    assert case["deviation"] == "behavioral_drift"
    assert case["verdict_source"] == "deterministic"
    assert case["confidence"] == 1.0
    assert case["observed_reality"]["escape_count"] == 3


async def test_the_tier_separates_two_reports_that_read_the_same(
    client: httpx.AsyncClient,
) -> None:
    """Both say a family drifted. One is backed by canary escapes — a
    structural fact about the probe output. The other is a consistency anomaly
    on an uncalibrated battery. The plane stored only the verdict string, so
    they were one piece of news."""
    await client.post(f"/v1/plane/{NODE}/probe-report", json=_battery())
    backed = (await client.get(f"/v1/plane/{NODE}/probe-report")).json()["drift_case"]

    await client.post(f"/v1/plane/{NODE}/probe-report",
                      json=_battery("CONSISTENCY_ANOMALY", escapes=0, session_id="s2"))
    anomaly = (await client.get(f"/v1/plane/{NODE}/probe-report")).json()["drift_case"]

    assert (backed["verdict_source"], backed["confidence"]) == ("deterministic", 1.0)
    assert anomaly["verdict_source"] == "judge"
    assert anomaly["confidence"] == pytest.approx(0.4)


async def test_the_uncalibrated_discount_survives_the_wire(
    client: httpx.AsyncClient,
) -> None:
    """The trap this projection exists to avoid: `health_payload` renames the
    drift score so no panel thresholds it, and feeding that dict to the grader
    unchanged loses it. Measured, the same report grades 0.05 — the floor —
    instead of 0.35."""
    posted = _battery("CONSISTENCY_ANOMALY", escapes=0, drift_score=0.7)
    await client.post(f"/v1/plane/{NODE}/probe-report", json=posted)
    case = (await client.get(f"/v1/plane/{NODE}/probe-report")).json()["drift_case"]

    assert case["confidence"] == pytest.approx(0.35)
    naive = BehavioralIntegrityAudit().evaluate(posted)  # what re-keying by hand skips
    assert naive is not None and naive.confidence == pytest.approx(0.05)


@pytest.mark.parametrize("verdict", ["CONSISTENT", "INCONCLUSIVE"])
async def test_a_verdict_that_is_not_a_deviation_grades_to_nothing(
    client: httpx.AsyncClient, verdict: str,
) -> None:
    await client.post(f"/v1/plane/{NODE}/probe-report",
                      json=_battery(verdict, escapes=0))
    assert (await client.get(f"/v1/plane/{NODE}/probe-report")).json()["drift_case"] is None


async def test_a_node_that_never_probed_has_no_grade(client: httpx.AsyncClient) -> None:
    """Absence of evidence, not a clean grade — the same rule the panel holds
    for the battery itself."""
    body = (await client.get("/v1/plane/never-probed/probe-report")).json()
    assert body["latest"] is None and body["drift_case"] is None


async def test_the_page_carries_the_tier(client: httpx.AsyncClient) -> None:
    """On-call gets the grade, not just the verdict string."""
    sent: list[tuple[str, str, dict]] = []

    class Spy:
        async def emit(self, trigger: str, node_id: str, payload: dict) -> None:
            sent.append((trigger, node_id, payload))

    client._app.state.notifier = Spy()
    await client.post(f"/v1/plane/{NODE}/probe-report", json=_battery())

    [(trigger, node, payload)] = sent
    assert trigger == "behavioral_drift" and node == NODE
    assert payload["verdict_source"] == "deterministic"
    assert payload["confidence"] == 1.0


# ── the line ──────────────────────────────────────────────────────────────────

async def test_drift_is_not_an_eval_metric(client: httpx.AsyncClient) -> None:
    """ui-spec 8.2: the health check must not be blended into Scenario Delta or
    Core scores. A battery has no run, so reaching the run-evidence path would
    mean inventing one — and that path ends in the must-block auto-pin and
    `safe_to_ship`. Three reports, every one of them drift-red, and the
    regression corpus must not move."""
    for i in range(3):
        r = await client.post(f"/v1/plane/{NODE}/probe-report",
                              json=_battery(session_id=f"s{i}"))
        assert r.status_code == 201

    assert (await client.get(f"/v1/plane/{NODE}/probe-report")).json()["drift_case"]
    pins = (await client.get("/v1/pins")).json()
    assert pins == {"pins": [], "must_block": 0, "must_pass": 0, "total": 0}
    assert (await client.get("/v1/runs")).json() == []


def test_the_case_says_it_is_not_a_score() -> None:
    """Stated in the payload, so a renderer does not have to know the deviation
    table to know this is not an integrity number."""
    case = drift_case(_battery())
    assert case["experimental"] is True and case["in_integrity_score"] is False
    assert DeviationType.BEHAVIORAL_DRIFT not in CORE_DEVIATIONS


# ── the two vocabularies, which cannot see each other ─────────────────────────
#
# P-34 forbids the import in BOTH directions, so neither library can check the
# other. axor-eval mirrors two of axor-probe's verdict constants as literals,
# and nothing anywhere notices when the sets diverge: measured, a fifth probe
# verdict ("REGIME_ESCAPE", 9 escapes of 10) grades to no case at all. This
# backend imports both, so this is the one place the check can live.

# Every probe verdict, and whether axor-eval treats it as a deviation. Adding a
# verdict to axor-probe fails the first assertion below until somebody decides
# which side of this line it belongs on.
VERDICT_MEANING: dict[str, bool] = {
    "CONSISTENT": False,
    "INCONCLUSIVE": False,
    "DRIFT_DETECTED": True,
    "CONSISTENCY_ANOMALY": True,
}


def test_every_probe_verdict_has_a_decided_meaning_in_eval() -> None:
    assert VERDICTS == set(VERDICT_MEANING), (
        "axor-probe's verdict vocabulary changed. axor-eval mirrors it as "
        "literals and cannot import it (P-34), so an unlisted verdict silently "
        "grades to 'no deviation' — decide which side it belongs on here."
    )


@pytest.mark.parametrize(("verdict", "is_deviation"), sorted(VERDICT_MEANING.items()))
def test_eval_grades_each_probe_verdict_the_way_this_table_says(
    verdict: str, is_deviation: bool,
) -> None:
    payload = audit_payload(_battery(verdict, escapes=3))
    case = BehavioralIntegrityAudit().evaluate(payload)
    assert (case is not None) is is_deviation
