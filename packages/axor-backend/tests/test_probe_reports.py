"""Behavioral health checks (ui-spec 8.2): the node posts a battery out-dial,
the plane stores it append-only and serves the last one plus the series.

The payloads here are the shape axor-probe's `integration.plane.health_payload`
emits. The backend never imports axor-probe — the shape is the whole contract.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.notifications import Notifier


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


def _payload(
    verdict: str = "CONSISTENT",
    families: list[dict] | None = None,
    escape_count: int = 0,
    session_id: str = "sess-1",
) -> dict:
    return {
        "session_id": session_id,
        "agent_id": "banking-assistant",
        "model": "m",
        "probe_library_version": "1.0.0",
        "overall_verdict": verdict,
        "families": families if families is not None else [
            {"family": "data_disclosure", "state": "clean", "escapes": 0, "probes": 3},
            {"family": "scope_expansion", "state": "clean", "escapes": 0, "probes": 3},
        ],
        "probes_sent": 6,
        "probes_invalid": 0,
        "probes_triangulated": 0,
        "structural_failures": 0,
        "escape_count": escape_count,
        "escape_rate": 0.0,
        "escape_rate_ci": [0.0, 0.0],
        "calibration_status": "UNCALIBRATED",
        "max_drift_score_uncalibrated": 0.0,
    }


async def test_no_check_yet_is_not_a_healthy_agent(client: httpx.AsyncClient) -> None:
    r = await client.get("/v1/plane/never-probed/probe-report")
    assert r.status_code == 200
    body = r.json()
    # Null, not an empty green — the panel must be able to tell "no evidence"
    # apart from "clean".
    assert body["latest"] is None
    assert body["history"] == []


async def test_report_round_trips_with_its_families(client: httpx.AsyncClient) -> None:
    posted = _payload(
        verdict="DRIFT_DETECTED",
        escape_count=1,
        families=[
            {"family": "data_disclosure", "state": "escaped", "escapes": 1, "probes": 3},
            {"family": "budget_bypass", "state": "clean", "escapes": 0, "probes": 3},
        ],
    )
    r = await client.post("/v1/plane/node-a/probe-report", json=posted)
    assert r.status_code == 201

    latest = (await client.get("/v1/plane/node-a/probe-report")).json()["latest"]
    assert latest["overall_verdict"] == "DRIFT_DETECTED"
    assert latest["escape_count"] == 1
    states = {f["family"]: f["state"] for f in latest["families"]}
    assert states == {"data_disclosure": "escaped", "budget_bypass": "clean"}
    # The uncalibrated severity number keeps its caveat through storage.
    assert "max_drift_score_uncalibrated" in latest
    assert latest["created_ts"]


async def test_checks_accumulate_oldest_first_for_the_sparkline(
    client: httpx.AsyncClient,
) -> None:
    await client.post("/v1/plane/node-b/probe-report",
                      json=_payload(verdict="DRIFT_DETECTED", escape_count=2))
    await client.post("/v1/plane/node-b/probe-report", json=_payload())

    body = (await client.get("/v1/plane/node-b/probe-report")).json()
    # A re-probe is a NEW check, never an overwrite: the drift that prompted the
    # heal has to stay readable next to the verification that followed it.
    assert [h["overall_verdict"] for h in body["history"]] == [
        "DRIFT_DETECTED", "CONSISTENT",
    ]
    assert body["latest"]["overall_verdict"] == "CONSISTENT"


async def test_reports_are_scoped_per_node(client: httpx.AsyncClient) -> None:
    await client.post("/v1/plane/node-c/probe-report", json=_payload())
    assert (await client.get("/v1/plane/node-d/probe-report")).json()["latest"] is None


async def test_unknown_verdict_is_rejected(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/plane/node-e/probe-report",
                          json=_payload(verdict="PROBABLY_FINE"))
    assert r.status_code == 400


async def test_unknown_family_state_is_rejected(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/v1/plane/node-f/probe-report",
        json=_payload(families=[{"family": "data_disclosure", "state": "amber"}]),
    )
    assert r.status_code == 400


async def test_drift_notifies_with_the_escaped_families(
    client: httpx.AsyncClient,
) -> None:
    sent: list[dict] = []

    async def capture(url: str, body: dict) -> int:
        sent.append(body)
        return 200

    notifier = Notifier(post=capture)
    notifier.subscribe("http://sink.test/hook", ["behavioral_drift"])
    client._app.state.notifier = notifier  # type: ignore[attr-defined]

    await client.post("/v1/plane/node-g/probe-report", json=_payload(
        verdict="DRIFT_DETECTED", escape_count=1,
        families=[
            {"family": "data_disclosure", "state": "escaped", "escapes": 1, "probes": 3},
            {"family": "budget_bypass", "state": "clean", "escapes": 0, "probes": 3},
        ],
    ))
    assert len(sent) == 1
    assert sent[0]["trigger"] == "behavioral_drift"
    assert sent[0]["node_id"] == "node-g"
    assert sent[0]["families"] == ["data_disclosure"]

    # A clean check is not news.
    await client.post("/v1/plane/node-g/probe-report", json=_payload())
    assert len(sent) == 1


class TestTheVocabularyIsProbesOwn:
    """It was two literals in `plane.py`, under a comment calling that "the same
    posture as everywhere else" — the opposite of what this backend does with
    its other neighbours, and their pyproject comments say why:

        axor-core     "a copy of a decoder is how a copy of a decision starts"
        axor-sentinel "Imported, not restated"

    The copy matched. Nothing checked that it did: `_PROBE_VERDICTS` appeared
    nowhere but that file, and nothing in axor-probe knew a control plane
    existed. A fifth verdict there and this route would have answered 400 to
    every report from an upgraded node, with no test in either repository
    noticing.
    """

    def test_the_route_validates_against_the_imported_set(self) -> None:
        """Asserted on the source, because the point is WHERE the vocabulary
        comes from: a test that only posted valid verdicts would pass against a
        literal that happens to agree today."""
        from axor_backend import plane as plane_module

        source = pathlib.Path(plane_module.__file__).read_text("utf-8")
        assert "from axor_probe.integration import plane as probe_plane" in source
        assert "probe_plane.VERDICTS" in source
        assert "probe_plane.FAMILY_STATES" in source
        assert "probe_plane.VERDICT_DRIFT_DETECTED" in source

    async def test_a_verdict_probe_does_not_know_is_refused(
        self, client: httpx.AsyncClient,
    ) -> None:
        r = await client.post("/v1/plane/node-v/probe-report",
                              json=_payload(verdict="TOTALLY_FINE"))
        assert r.status_code == 400
        assert "overall_verdict must be one of" in r.json()["detail"]

    async def test_every_verdict_probe_can_produce_is_accepted(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The property the import buys: this list is not maintained here, so it
        cannot fall behind the one the node posts."""
        from axor_probe.integration.plane import VERDICTS

        for verdict in sorted(VERDICTS):
            r = await client.post("/v1/plane/node-v/probe-report",
                                  json=_payload(verdict=verdict))
            assert r.status_code == 201, (verdict, r.text)

    async def test_every_family_state_probe_can_produce_is_accepted(
        self, client: httpx.AsyncClient,
    ) -> None:
        from axor_probe.integration.plane import FAMILY_STATES

        families = [{"family": f"f{i}", "state": state, "escapes": 0, "probes": 1}
                    for i, state in enumerate(sorted(FAMILY_STATES))]
        r = await client.post("/v1/plane/node-f/probe-report",
                              json=_payload(families=families))
        assert r.status_code == 201, r.text

    async def test_a_verdict_added_to_probe_needs_no_change_here(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The property, stated directly.

        A mutation that adds a fifth verdict to axor-probe cannot fail any test
        in this file — there is no copy for it to fall behind. That is the fix,
        so it is asserted rather than left as an absence: give probe a verdict
        this repository has never heard of, and the route takes it.

        Against the old literal this was a 400 on every report from an upgraded
        node, with no test in either repository noticing.
        """
        from axor_probe.integration import plane as probe_plane

        grown = frozenset({*probe_plane.VERDICTS, "SOMETHING_NEW"})
        monkeypatch.setattr(probe_plane, "VERDICTS", grown)
        r = await client.post("/v1/plane/node-n/probe-report",
                              json=_payload(verdict="SOMETHING_NEW"))
        assert r.status_code == 201, r.text

    async def test_a_state_added_to_probe_needs_no_change_here(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from axor_probe.integration import plane as probe_plane

        grown = frozenset({*probe_plane.FAMILY_STATES, "quarantined"})
        monkeypatch.setattr(probe_plane, "FAMILY_STATES", grown)
        r = await client.post("/v1/plane/node-n/probe-report", json=_payload(
            families=[{"family": "f", "state": "quarantined",
                       "escapes": 0, "probes": 1}]))
        assert r.status_code == 201, r.text

    async def test_a_real_health_payload_is_accepted_as_posted(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The end of the argument: a payload built by axor-probe's own report
        and its own projection, not a hand-written fixture of what one might
        look like. If the two sides ever disagree about the shape, this is
        where it shows — on the route, not in a comment."""
        import time

        from axor_probe.comparator.scorer import ComparisonMode
        from axor_probe.integration.plane import health_payload
        from axor_probe.probes.schema import ProbeType
        from axor_probe.signals.drift import DriftAction, DriftSignal
        from axor_probe.signals.report import ProbeReport

        signal = DriftSignal(
            signal_id="sig1", probe_id="p1", probe_library_version="1.0.0",
            snapshot_id="snap1", session_id="sess-r", agent_id="agent-r",
            probe_type=ProbeType.DATA_DISCLOSURE,
            divergence_category=None, drift_score=0.6,
            comparator_confidence=1.0, comparison_mode=ComparisonMode.BINARY,
            triangulation_result=None, field_divergences=(),
            snapshot_payload={"decision": "disclose"},
            shadow_payload={"decision": "decline"},
            shadow_baseline_payload=None, calibration_status="UNCALIBRATED",
            timestamp=time.time(),
            recommended_action=DriftAction.from_escape(True),
            escape_detected=True,
        )
        report = ProbeReport.build(
            session_id="sess-r", agent_id="agent-r", model="m",
            probe_library_version="1.0.0", drift_signals=[signal], timeline=[],
            probes_sent=1, probes_invalid=0, probes_triangulated=0,
            summary_calibration_anomalies=0, consistency_anomaly_detected=False,
            calibration_status="UNCALIBRATED",
        )
        payload = health_payload(report)
        r = await client.post("/v1/plane/node-r/probe-report", json=payload)
        assert r.status_code == 201, r.text
        latest = (await client.get(
            "/v1/plane/node-r/probe-report")).json()["latest"]
        assert latest["overall_verdict"] == payload["overall_verdict"]
        assert {f["state"] for f in latest["families"]} == {
            f["state"] for f in payload["families"]}
