"""CP → Axor Lab: /v1/runs/{run_id}/lab-package (axor-lab-incident/v1).

The exported package must be the real thing: every artifact valid against the
INSTALLED axor-lab contracts (schema + semantics), and the trace must REPLAY
under its recorded condition through the Lab's own import path — the same
checks `axor-lab import-incident` runs. A run without the required depth is
refused with the complete reason list, never exported as something the Lab
would misreproduce.

Requires axor-lab installed (pip install -e /path/to/axor-lab); skipped
otherwise.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app

lab_contracts = pytest.importorskip("lab_contracts")


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


async def _seeded_package(client: httpx.AsyncClient, run_id: str = "ex_block") -> dict:
    assert (await client.post("/v1/demo/seed-adapter-runs")).status_code == 200
    r = await client.get(f"/v1/runs/{run_id}/lab-package")
    assert r.status_code == 200, r.text
    return r.json()


async def test_package_shape_and_source(client: httpx.AsyncClient) -> None:
    pkg = await _seeded_package(client)
    assert pkg["schema_version"] == "axor-lab-incident/v1"
    assert set(pkg) == {"schema_version", "trace", "scenario", "manifests",
                        "condition", "source"}
    assert pkg["source"]["product"] == "control-plane"
    assert pkg["source"]["run_id"] == "ex_block"
    assert pkg["source"]["url"] == "/v1/runs/ex_block"


async def test_artifacts_valid_against_installed_lab_contracts(
    client: httpx.AsyncClient,
) -> None:
    """Schema + semantic validation with the REAL installed axor-lab — the
    exact validators `axor-lab import-incident` runs first."""
    pkg = await _seeded_package(client)
    for name in ("trace", "scenario", "condition"):
        assert lab_contracts.validate_artifact(pkg[name], name) == [], name
    for manifest in pkg["manifests"]:
        assert lab_contracts.validate_artifact(manifest, "tool-manifest") == []
    lab_contracts.validate_scenario(
        pkg["scenario"], {m["id"]: m for m in pkg["manifests"]}
    )  # raises ScenarioValidationError on any failure


async def test_recorded_condition_is_bound(client: httpx.AsyncClient) -> None:
    pkg = await _seeded_package(client)
    condition = pkg["condition"]
    trace = pkg["trace"]
    assert condition["enforcement"] == "on"
    assert condition["id"] == trace["trial"]["condition_id"]
    assert pkg["scenario"]["name"] == trace["trial"]["scenario_id"]
    # config_hash is the Lab's own reproducibility anchor, byte-identical
    assert condition["config_hash"] == lab_contracts.condition_config_hash(
        condition["kernel"], condition.get("policy")
    )
    # the axor-core build that produced the verdicts is recorded as the shared
    # kernel identity; `kernel` pins the label-based replay backend
    assert str(condition["kernel_ref"]).startswith("axor-core@")
    assert trace["producer"]["kernel_version"] == condition["kernel"]


async def test_package_round_trips_through_lab_import(
    client: httpx.AsyncClient,
) -> None:
    """The acceptance core: the package imports through the Lab's OWN
    import-incident path (validate + config hash + REPLAY before write) and
    replays bit-identically under its recorded condition."""
    incident = pytest.importorskip("lab_runner.incident")
    pkg = await _seeded_package(client)
    result = incident.import_incident(
        pkg["trace"], pkg["scenario"], pkg["manifests"], pkg["condition"]
    )
    assert result.replay_status == "match"
    assert result.trace_id == "cp-ex_block"
    # the recorded DENY (the caught exfil) is in the imported trace
    verdicts = [
        e["decision"]["verdict"] for e in result.trace["events"]
        if e.get("type") == "gate_decision"
    ]
    assert verdicts.count("DENY") == 1 and verdicts[-1] == "DENY"


async def test_package_is_deterministic(client: httpx.AsyncClient) -> None:
    first = await _seeded_package(client)
    second = (await client.get("/v1/runs/ex_block/lab-package")).json()
    assert first == second


async def test_pass_only_run_is_refused_with_reasons(
    client: httpx.AsyncClient,
) -> None:
    await client.post("/v1/demo/seed-adapter-runs")
    r = await client.get("/v1/runs/ex_pass/lab-package")
    assert r.status_code == 422
    detail = r.json()["detail"]
    reasons = "\n".join(detail["reasons"])
    assert "injection vector" in reasons
    assert "sink" in reasons


async def test_tree_run_message_denial_is_refused(client: httpx.AsyncClient) -> None:
    await client.post("/v1/demo/seed-tree-run")
    r = await client.get("/v1/runs/ex_tree/lab-package")
    assert r.status_code == 422
    reasons = "\n".join(r.json()["detail"]["reasons"])
    assert "message-gate DENY" in reasons


async def test_unknown_run_404(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/runs/nope/lab-package")).status_code == 404


async def test_proxy_depth_run_is_refused(client: httpx.AsyncClient) -> None:
    """A run of plane telemetry only (no kernel-schema lines) is honestly
    refused — the Lab needs the adapter trace depth."""
    await client.post("/v1/ingest/shallow", json={
        "node_id": "n1", "events": [{"seq": 0, "kind": "heartbeat",
                                     "payload": {}}],
    })
    r = await client.get("/v1/runs/shallow/lab-package")
    assert r.status_code == 422
    assert "kernel-schema" in "\n".join(r.json()["detail"]["reasons"])
