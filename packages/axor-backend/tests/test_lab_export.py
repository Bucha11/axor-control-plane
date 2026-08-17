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

import json
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
                        "condition", "replay_fidelity", "source"}
    assert pkg["source"]["product"] == "control-plane"
    assert pkg["source"]["run_id"] == "ex_block"
    assert pkg["source"]["url"] == "/v1/runs/ex_block"


async def test_replay_fidelity_is_honest_per_gate(client: httpx.AsyncClient) -> None:
    """The incident carries an explicit per-gate fidelity statement: taint_floor
    reproduces faithfully, content-inspecting gates do not (the Control Plane
    records observations, not payload bodies)."""
    fidelity = (await _seeded_package(client))["replay_fidelity"]
    assert fidelity["reproducible_gates"] == ["taint_floor"]
    assert "ssrf" in fidelity["not_reproducible_gates"]
    assert "value_policy" in fidelity["not_reproducible_gates"]
    assert str(fidelity["recorded_kernel"]).startswith("axor-core@")
    assert "observation-only" in fidelity["note"]


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
    client: httpx.AsyncClient, tmp_path: pathlib.Path,
) -> None:
    """The acceptance core: the package imports through the Lab's OWN
    import-incident path (validate + config hash + REPLAY before write) and
    replays bit-identically under its recorded condition.

    This used to `importorskip("lab_runner.incident")`. That module has never
    existed under any name — the import path is the `axor-lab import-incident`
    COMMAND — so the acceptance core of this file skipped on every run,
    including on a machine with axor-lab fully installed. It drives the real CLI
    now, which is the only entry point the feature actually has.
    """
    cli = pytest.importorskip("lab_runner.cli")
    pkg = await _seeded_package(client)
    for name in ("trace", "scenario", "manifests", "condition"):
        (tmp_path / f"{name}.json").write_text(json.dumps(pkg[name]))
    code = cli.main([
        "import-incident",
        "--trace", str(tmp_path / "trace.json"),
        "--scenario", str(tmp_path / "scenario.json"),
        "--manifests", str(tmp_path / "manifests.json"),
        "--condition", str(tmp_path / "condition.json"),
        "--out", str(tmp_path / "bundle"),
    ])
    # exit 0 means it validated, hashed, AND replayed to `match` — the command
    # refuses to write otherwise
    assert code == 0
    bundle = json.loads((tmp_path / "bundle" / "bundle.json").read_text())
    imported = json.loads(
        next((tmp_path / "bundle" / "traces").glob("*.json")).read_text()
    )
    assert str(imported["trace_id"]) == "cp-ex_block"
    assert bundle["trials"]
    # the recorded DENY (the caught exfil) survived the round trip
    verdicts = [
        e["decision"]["verdict"] for e in imported["events"]
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
