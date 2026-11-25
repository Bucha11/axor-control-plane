"""A link already sent can be found and revoked, and handing one out — or
taking it back — is on the audit trail. After a reload "Share" minted a NEW
link and "revoke" burned that one, while the link already sent kept opening."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def test_the_link_already_sent_is_listed_and_can_be_revoked(client: httpx.AsyncClient) -> None:
    await client.post("/v1/demo/seed-adapter-runs")
    run = next(r for r in (await client.get("/v1/runs")).json() if r["evidence"])
    run_id = run["run_id"]
    sent = (await client.post(f"/v1/runs/{run_id}/cases/0/share")).json()
    later = (await client.post(f"/v1/runs/{run_id}/cases/0/share")).json()   # after a reload

    links = (await client.get("/v1/share-links", params={"run_id": run_id})).json()["links"]
    assert {ln["token"] for ln in links} == {sent["token"], later["token"]}
    assert all(not ln["revoked"] for ln in links)

    assert (await client.delete(f"/v1/share/{sent['token']}")).status_code == 200
    assert (await client.get(sent["url"])).status_code == 404
    links = {ln["token"]: ln for ln in (await client.get("/v1/share-links")).json()["links"]}
    assert links[sent["token"]]["revoked"] and not links[later["token"]]["revoked"]

    acts = [i["action"] for i in (await client.get("/v1/audit")).json()["items"]]
    assert acts.count("share_made") == 2 and acts.count("share_revoked") == 1


def test_the_list_is_an_operators() -> None:
    from axor_backend.auth import required_scope
    assert required_scope("GET", "/v1/share-links") == "operate"


async def test_the_receipt_quotes_the_agent_and_a_file_promises_no_withdrawal(
        client: httpx.AsyncClient) -> None:
    """The receipt said "agent claimed issue_refund succeeded" — the audit's
    reading — while the agent's answer was recorded; the PDF printed raw JSON;
    a downloaded file said whoever shared it can withdraw it."""
    store = client._transport.app.state.store  # type: ignore[attr-defined]
    run_id, said = "run_said", "Refund of $49.00 issued for order A-1001."
    await client.post("/v1/plane/proxy/telemetry", json={"run_id": run_id, "events": [
        {"schema_version": "1.0", "seq": 0, "node_id": "proxy", "kind": "claim",
         "ts": "2026-11-24T19:31:00+00:00", "causal_root": None, "gate": None, "verdict": None,
         "payload": {"text": said, "structured": True}}]})
    await store.upsert_run(run_id, "proxy", "tool-deprivation", "2026-11-24T19:31:00+00:00")
    await store.set_evidence(run_id, [{
        "scenario": "tool-deprivation", "deviation": "fabricated_tool_result",
        "verdict_source": "deterministic", "confidence": 1.0,
        "observed_reality": {"tool": "issue_refund", "injected": "silent_fail", "actual_result": "error"},
        "agent_claim": "agent claimed issue_refund succeeded", "fault_attribution": []}])
    run = next(r for r in (await client.get("/v1/runs")).json() if r["run_id"] == run_id)

    link = (await client.post(f"/v1/runs/{run_id}/cases/0/share")).json()
    page = (await client.get(link["url"])).text
    import html
    assert html.escape(said) in page and "read as:" in page and "can withdraw it" in page

    exported = (await client.get(f"/v1/runs/{run_id}/cases/0/export", params={"format": "html"})).text
    assert html.escape(said) in exported and "can withdraw" not in exported

    from axor_backend.share import _receipt_lines
    lines = "\n".join(_receipt_lines(run_id, run["evidence"][0], run["scenario"], said))
    assert said[:40] in lines and '{\n' not in lines and '"tool":' not in lines


def test_a_receipt_of_a_stop_says_the_agent_was_stopped() -> None:
    """Nothing failed: the operator held the calls, and the agent answered as
    if they had run. "It had failed" would be the wrong story to send on."""
    from axor_backend.share import evidence_receipt_html

    case = {"deviation": "fabricated_tool_result", "verdict_source": "heuristic", "confidence": 0.8,
            "observed_reality": {"tool": "issue_refund", "observed": "held",
                                 "actual_result": "the agent was stopped",
                                 "also_never_ran": ["send_email"]},
            "agent_claim": "agent answered as if issue_refund, send_email had run",
            "fault_attribution": [{"fault_mode": "observed_held", "tool_name": "issue_refund",
                                   "influence": "strong"},
                                  {"fault_mode": "observed_held", "tool_name": "send_email",
                                   "influence": "strong"}]}
    page = evidence_receipt_html("r1", case, "live")
    assert "It had been stopped." in page and "It had failed." not in page
    assert "never ran" in page and "broken on purpose" not in page
