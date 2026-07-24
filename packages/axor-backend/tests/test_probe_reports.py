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
