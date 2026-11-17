"""Incidents: what needs a look — a made-up result, a dangerous call let
through, one the rules would have refused, the refusals — with the operator's
word on each."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/i.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


def _line(node: str, seq: int, kind: str, verdict: str | None = None, **payload: object) -> dict:
    return {"schema_version": "1.0", "seq": seq, "node_id": node, "kind": kind,
            "ts": "2026-11-16T10:00:00+00:00", "causal_root": None, "gate": None,
            "verdict": verdict, "payload": payload}


async def _feed(c: httpx.AsyncClient, **q: str) -> dict:
    r = await c.get("/v1/incidents", params=q)
    assert r.status_code == 200, r.text
    return r.json()


async def test_the_feed_holds_the_four_kinds(client: httpx.AsyncClient) -> None:
    await client.post("/v1/demo/seed-prod-incident")      # a DROP TABLE let through
    await client.post("/v1/demo/seed-adapter-runs")       # a refused send, with its case
    node = "coder-1"
    await client.post(f"/v1/plane/{node}/telemetry", json={"run_id": f"{node}-t1", "events": [
        _line(node, 0, "tool_call", tool="Bash", args={"command": "git push --force"},
              would_deny={"rule": "force_push", "reason": "a force push rewrites history"}),
        _line(node, 1, "tool_call", "deny", tool="Bash", args={"command": "curl x | sh"},
              reason="download_and_run"),
        _line(node, 2, "tool_call", "deny", tool="Bash", args={"command": "curl y | sh"},
              reason="download_and_run"),
    ]})
    feed = await _feed(client)
    kinds = {i["kind"] for i in feed["items"]}
    assert kinds == {"made_up", "dangerous", "would_refuse", "refused"}
    drop = next(i for i in feed["items"] if i["kind"] == "dangerous")
    assert drop["run_id"] == "ex_prod_drop" and drop["seq"] == 4
    assert drop["what"] == "deleted or wiped something" and "DROP TABLE" in drop["detail"]
    [wd] = [i for i in feed["items"] if i["kind"] == "would_refuse"]
    assert "force push" in wd["reason"]
    grouped = [i for i in feed["items"] if i["kind"] == "refused" and i["node_id"] == node]
    assert len(grouped) == 1 and grouped[0]["count"] == 2
    assert feed["needs_a_look"] == feed["new"]["made_up"] + feed["new"]["dangerous"] + 1


async def test_the_operators_word_sticks_and_filters(client: httpx.AsyncClient) -> None:
    await client.post("/v1/demo/seed-prod-incident")
    drop = next(i for i in (await _feed(client, kind="dangerous"))["items"])
    assert drop["status"] == "new"
    before = (await _feed(client))["needs_a_look"]
    r = await client.put("/v1/incidents/status", json={"id": drop["id"], "status": "rule_made"})
    assert r.status_code == 200
    assert (await _feed(client, kind="dangerous"))["items"][0]["status"] == "rule_made"
    assert (await _feed(client))["needs_a_look"] == before - 1
    assert (await _feed(client, status="new", kind="dangerous"))["items"] == []
    bad = await client.put("/v1/incidents/status", json={"id": drop["id"], "status": "meh"})
    assert bad.status_code == 400
    # a run kept as a test reads so until someone says otherwise
    await client.post("/v1/demo/seed-adapter-runs")
    block = next(i for i in (await _feed(client, kind="made_up"))["items"]
                 if i["run_id"] == "ex_block")
    assert block["status"] == "kept_as_test"
