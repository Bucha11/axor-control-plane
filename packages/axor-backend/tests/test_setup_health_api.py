"""Coding agents' setup health: posted by axor_eval.setup_health, read back."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend import demo
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


def _report() -> dict:
    return {"kind": "setup-health", "agent": "claude", "trials": 3, "setup": [],
            "families_raw": {"beyond_task": [1, 3]}, "baseline_raw": {},
            "causes": [], "runs": [{"probe": "p", "evidence": "x" * 5000}],
            "verdict": "first_check"}


async def test_post_then_latest_is_the_next_baseline(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/setup-health/latest")).json() == {"check": None}
    r = await client.post("/v1/setup-health", json=_report())
    assert r.status_code == 201
    cid = r.json()["check_id"]
    latest = (await client.get("/v1/setup-health/latest")).json()["check"]
    assert latest["check_id"] == cid
    assert latest["families_raw"] == {"beyond_task": [1, 3]}
    assert len(latest["runs"][0]["evidence"]) <= 300   # long run text is cut


async def test_not_a_report_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/setup-health", json={"kind": "skill-eval"})).status_code == 400


async def test_example_is_worse_and_traced(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/demo/seed-setup-health")).status_code == 200
    await client.post("/v1/demo/seed-setup-health")   # idempotent
    checks = (await client.get("/v1/setup-health")).json()["checks"]
    assert [c["verdict"] for c in checks] == ["worse", "first_check"]
    latest = checks[0]
    assert {c["fragment"] for c in latest["causes"]} == {"skill:fetch-docs",
                                                          "CLAUDE.md#Workflow"}
    assert [r["family"] for r in latest["rows"] if r["worse"]] == ["runs_fetched_code",
                                                                    "beyond_task"]


def test_example_rows_match_the_eval_rule() -> None:
    before, latest = demo.setup_health_examples()
    assert before["verdict"] == "first_check" and latest["example"]
