"""The workspace's audit trail: who paused, stopped or capped which agent, who
pulled the brake, granted an exception, set the seat limit — read newest first,
filtered, and exported for an archive."""
from __future__ import annotations

import csv
import io
import json
import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.ee.license import License
from axor_backend.tenancy import PUBLIC_ORG


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/a.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        # the whole trail and its export are the Business plan's
        app.state.licenses[PUBLIC_ORG] = License(PUBLIC_ORG, "business", 1, "2999-01-01")
        c.app = app  # type: ignore[attr-defined]
        yield c


async def _session(c: httpx.AsyncClient, node: str) -> None:
    await c.post(f"/v1/plane/{node}/profile", json={
        "kind": "coding", "agent": "claude-code", "developer": "dev-ada"})
    await c.post(f"/v1/plane/{node}/telemetry", json={"run_id": f"{node}-hb", "events": [
        {"seq": 0, "kind": "heartbeat", "payload": {"applied_version": 0, "level": "NORMAL",
                                                    "budget_remaining": None}}]})


async def test_controls_are_on_the_trail(client: httpx.AsyncClient) -> None:
    node = "claude-code-audit-1"
    await _session(client, node)
    await client.post(f"/v1/plane/{node}/command", json={"version": 1, "state": {"paused": True}})
    await client.post(f"/v1/plane/{node}/command", json={"version": 2, "state": {"paused": False}})
    await client.post(f"/v1/plane/{node}/command",
                      json={"version": 3, "state": {"budget_cap_calls": 40}})
    await client.post(f"/v1/plane/{node}/command", json={"version": 4, "state": {"stopped": True}})
    await client.put("/v1/coding/brake", json={"on": True, "reason": "a skill gone bad"})
    await client.put("/v1/coding/brake", json={"on": False})
    await client.post(f"/v1/coding/say/{node}", json={"text": "use the existing rate limiter " * 5})
    body = (await client.get("/v1/audit")).json()
    acts = [(r["action"], r["target"]) for r in body["items"]]
    assert acts[::-1] == [("pause", node), ("resume", node), ("budget", node), ("stop", node),
                          ("brake_on", "every coding agent"), ("brake_off", "every coding agent"),
                          ("say", node)]
    brake = next(r for r in body["items"] if r["action"] == "brake_on")
    assert brake["detail"] == {"reason": "a skill gone bad"} and brake["who"] == "operator"
    budget = next(r for r in body["items"] if r["action"] == "budget")
    assert budget["detail"] == {"calls": 40}
    # a note is on the trail by its first words, not whole
    say = next(r for r in body["items"] if r["action"] == "say")
    assert len(say["detail"]["text"]) <= 60 and say["detail"]["text"].endswith("…")
    # a refused command is not an act
    assert (await client.post(f"/v1/plane/{node}/command",
                              json={"version": 1, "state": {"paused": True}})).status_code == 409
    assert (await client.get("/v1/audit")).json()["total"] == 7


async def test_a_rules_save_is_said_change_by_change(client: httpx.AsyncClient) -> None:
    await client.put("/v1/coding/rules", json={
        "allow": [], "mode": "enforce",
        "exceptions": [{"rule": "secrets", "path": "tests/fixtures/*", "note": "fake keys"}],
        "trusted_sources": ["docs.internal.dev"]})
    await client.put("/v1/coding/rules", json={
        "allow": [], "mode": "observe", "exceptions": [], "trusted_sources": ["docs.internal.dev"]})
    items = (await client.get("/v1/audit")).json()["items"]
    acts = [(r["action"], r["target"]) for r in items][::-1]
    assert acts == [("exception_granted", "secrets: tests/fixtures/*"),
                    ("source_trusted", "docs.internal.dev"),
                    ("rules_mode", "observe"),
                    ("exception_withdrawn", "secrets: tests/fixtures/*")]
    assert items[-1]["detail"] == {"note": "fake keys"}


async def test_filtered_and_exported(client: httpx.AsyncClient) -> None:
    for n in ("claude-code-a", "claude-code-b"):
        await _session(client, n)
        await client.post(f"/v1/plane/{n}/command", json={"version": 1, "state": {"paused": True}})
    await client.post("/v1/plane/claude-code-a/command",
                      json={"version": 2, "state": {"stopped": True}})
    only = (await client.get("/v1/audit", params={"action": "pause"})).json()
    # QA-020: the choices are every act on the trail, whichever one is picked
    assert only["total"] == 2 and set(only["actions"]) == {"pause", "stop"}
    assert (await client.get("/v1/audit", params={"target": "code-b"})).json()["total"] == 1
    r = await client.get("/v1/audit/export", params={"format": "csv"})
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["action"] for x in rows] == ["stop", "pause", "pause"]
    j = await client.get("/v1/audit/export", params={"format": "jsonl"})
    lines = [json.loads(x) for x in j.text.splitlines()]
    assert lines[0]["action"] == "stop" and lines[0]["target"] == "claude-code-a"
    # a range: nothing before the first act
    assert (await client.get("/v1/audit", params={"until": "2000-01-01"})).json()["total"] == 0
    assert (await client.get("/v1/audit/export", params={"format": "xml"})).status_code == 400


async def test_key_acts_are_on_the_same_trail(client: httpx.AsyncClient) -> None:
    made = (await client.post("/v1/keys", json={"scopes": ["ingest"], "label": "ci"})).json()
    items = (await client.get("/v1/audit")).json()["items"]
    assert items[0]["action"] == "key_mint" and items[0]["target"] == made["key_id"]
    assert items[0]["detail"]["scopes"] == ["ingest"]
    # and without them, when asked for controls only
    assert (await client.get("/v1/audit", params={"kinds": "control"})).json()["total"] == 0


async def test_below_business_the_trail_is_the_last_30_days_and_not_exported(
        client: httpx.AsyncClient) -> None:
    del client.app.state.licenses[PUBLIC_ORG]  # type: ignore[attr-defined]
    await client.put("/v1/coding/brake", json={"on": True})
    old = {"ts": "2020-01-01T00:00:00+00:00", "action": "pause", "target": "x",
           "by": {"kind": "open", "who": "operator"}}
    await client.app.state.store.add_vault_audit("control", old, old["ts"])  # type: ignore[attr-defined]
    body = (await client.get("/v1/audit")).json()
    assert body["window_days"] == 30
    assert [r["action"] for r in body["items"]] == ["brake_on"]
    r = await client.get("/v1/audit/export")
    assert r.status_code == 402 and r.json()["detail"]["plan"] == "business"


async def test_the_workspace_keeps_its_trail_as_long_as_it_says(client: httpx.AsyncClient) -> None:
    store = client.app.state.store  # type: ignore[attr-defined]
    old = {"ts": "2020-01-01T00:00:00+00:00", "action": "pause", "target": "x",
           "by": {"kind": "open", "who": "operator"}}
    await store.add_vault_audit("control", old, old["ts"])
    await client.put("/v1/coding/brake", json={"on": True})
    assert (await client.put("/v1/audit/retention", json={"days": 7})).status_code == 400
    r = await client.put("/v1/audit/retention", json={"days": 90})
    assert r.status_code == 200 and r.json() == {"days": 90, "pruned": 1}
    acts = [i["action"] for i in (await client.get("/v1/audit")).json()["items"]]
    assert "pause" not in acts and acts[0] == "audit_retention"
    assert (await client.get("/v1/audit/retention")).json() == {"days": 90}


async def test_a_month_s_report_says_who_acted_on_whose_agent(client: httpx.AsyncClient) -> None:
    node = "claude-code-report-1"
    await _session(client, node)            # dev-ada's: no address, nobody's
    await client.post(f"/v1/plane/{node}/command", json={"version": 1, "state": {"paused": True}})
    await client.put("/v1/coding/brake", json={"on": True, "reason": "incident 42"})
    await client.put("/v1/coding/rules", json={
        "allow": [], "exceptions": [{"rule": "secrets", "path": "tests/fixtures/*"}]})
    report = (await client.get("/v1/audit/report")).json()
    assert report["acts"] == 3 and report["by_action"]["pause"] == 1
    assert report["by_person"]["operator"]["brake_on"] == 1
    assert report["brakes"][0]["detail"] == {"reason": "incident 42"}
    assert report["policy_changes"][0]["target"] == "secrets: tests/fixtures/*"
    assert report["on_others_sessions"] == []
    assert (await client.get("/v1/audit/report", params={"month": "May"})).status_code == 400
    assert (await client.get("/v1/audit/report",
                             params={"month": "2020-01"})).json()["acts"] == 0
