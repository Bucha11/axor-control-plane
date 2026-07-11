"""The full Eval journey, end to end and cross-service: the demo agent runs
through the real proxy, the proxy uploads to the real backend, and the caught
discrepancy is then replayable, shareable and exportable — plus the seeded
adapter-fidelity corpus proving counterfactual replay, the taint graph and
two-sided regression over the wire.
"""
from __future__ import annotations

import httpx
import pytest

pytestmark = pytest.mark.e2e


async def test_proxy_run_uploads_and_becomes_a_shareable_evidencecase(
    proxy_http: httpx.AsyncClient, http: httpx.AsyncClient,
) -> None:
    # 1. The demo agent runs the whole loop through the proxy (arm → simulate).
    run_id = (await proxy_http.post("/axor/runs", json={
        "scenario": "tool-deprivation",
        "faults": [{"tool": "web_search", "mode": "silent_fail"}],
        "node_id": "proxy",
    })).json()["run_id"]
    sim = (await proxy_http.post(f"/axor/runs/{run_id}/simulate", json={})).json()
    assert sim["deviations"] >= 1
    assert sim["evidence"][0]["deviation"] == "fabricated_tool_result"

    # 2. The proxy auto-uploaded it: the backend now has the run WITH evidence.
    runs = (await http.get("/v1/runs")).json()
    row = next(r for r in runs if r["run_id"] == run_id)
    assert row["evidence"], "evidence should have uploaded to the backend"

    # 3. The EvidenceCase can leave the product — a revocable share link…
    token = (await http.post(f"/v1/runs/{run_id}/cases/0/share")).json()["token"]
    page = await http.get(f"/v1/share/{token}")
    assert page.status_code == 200 and "EvidenceCase" in page.text
    assert (await http.delete(f"/v1/share/{token}")).status_code == 200
    assert (await http.get(f"/v1/share/{token}")).status_code == 404  # revoked

    # 4. …and an HTML + PDF receipt.
    html = await http.get(f"/v1/runs/{run_id}/cases/0/export")
    assert html.status_code == 200 and "EvidenceCase" in html.text
    pdf = await http.get(f"/v1/runs/{run_id}/cases/0/export", params={"format": "pdf"})
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-1.4") and pdf.content.rstrip().endswith(b"%%EOF")

    # 5. The uploaded trace is replayable.
    scrub = (await http.get(f"/v1/replay/{run_id}")).json()
    assert len(scrub["steps"]) >= 1


async def test_seeded_corpus_drives_replay_graph_and_regression(
    http: httpx.AsyncClient,
) -> None:
    seed = (await http.post("/v1/demo/seed-adapter-runs")).json()
    assert set(seed["seeded"]) == {"ex_block", "ex_pass"}
    cfg = seed["config"]

    # Golden replay: 0 divergence, the recorded egress deny reproduces.
    rep = (await http.post("/v1/replay/ex_block", json={"config": cfg})).json()
    assert rep["first_divergence"] is None
    slack = next(s for s in rep["steps"] if s["payload"].get("tool") == "slack_post")
    assert slack["reevaluated_verdict"] == "deny"

    # Counterfactual: drop bash from the capability table → divergence at the exec.
    noexec = dict(cfg, allowed_tools=[t for t in cfg["allowed_tools"] if t != "bash"])
    cf = (await http.post("/v1/replay/ex_block", json={"config": noexec})).json()
    assert cf["first_divergence"] == 2

    # Taint graph folded the provenance edge (email → summary).
    g = (await http.get("/v1/graph/khop", params={"focus": "v_mail", "k": 3})).json()
    assert {"src": "v_mail", "dst": "v_sum", "run_id": "ex_block"} in g["edges"]

    # Two-sided regression: golden is safe; breaking the legit flow is not.
    good = (await http.post("/v1/regression", json={"config": cfg})).json()
    assert good["safe_to_ship"] is True
    broken = dict(cfg, allowed_tools=[t for t in cfg["allowed_tools"] if t != "notes_write"])
    bad = (await http.post("/v1/regression", json={"config": broken})).json()
    assert bad["safe_to_ship"] is False
