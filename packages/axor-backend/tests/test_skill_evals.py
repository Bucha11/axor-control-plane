"""Skill evals on the plane: axor-eval posts its report (`--plane`), the plane
stores it as written and serves it back — and has an example to show."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app

REPORT = {
    "agent": "claude-code", "model": "claude-sonnet-5-5",
    "skills": ["changelog-entries", "test-first"],
    "arms": [{"arm": "none", "skills": []},
             {"arm": "all", "skills": ["changelog-entries", "test-first"]}],
    "per_skill": [{"skill": "changelog-entries", "verdict": "helps"},
                  {"skill": "test-first", "verdict": "costs tokens, no gain seen"}],
    "overall": {"task": "all tasks", "arms": [], "vs_none": []},
    "per_task": [{"task": "fix-and-record", "arms": [], "vs_none": []}],
    "trials": [{"task": "fix-and-record", "arm": "none", "answer": "x" * 5000,
                "check_output": "y" * 5000, "solved": False}],
}


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/e.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def test_a_report_is_stored_as_written_and_listed_by_its_verdicts(
        client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/skill-evals", json=REPORT)
    assert r.status_code == 201, r.text
    eval_id = r.json()["eval_id"]
    [row] = (await client.get("/v1/skill-evals")).json()["evals"]
    assert row["eval_id"] == eval_id and row["runs"] == 1 and row["tasks"] == 1
    assert row["verdicts"] == {"changelog-entries": "helps",
                               "test-first": "costs tokens, no gain seen"}
    got = (await client.get(f"/v1/skill-evals/{eval_id}")).json()
    assert got["per_skill"] == REPORT["per_skill"] and got["example"] is False
    # an answer and a check output are evidence, not a log store
    assert len(got["trials"][0]["answer"]) == 600 and len(got["trials"][0]["check_output"]) == 600
    # re-posting under the same id replaces it
    again = await client.post("/v1/skill-evals",
                              json={**REPORT, "eval_id": eval_id, "model": "m2"})
    assert again.status_code == 201
    assert (await client.get(f"/v1/skill-evals/{eval_id}")).json()["model"] == "m2"


async def test_a_report_that_is_not_one_is_refused(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/skill-evals", json={"skills": []})).status_code == 400
    assert (await client.post("/v1/skill-evals", json={**REPORT, "trials": {}})).status_code == 400
    bad_id = await client.post("/v1/skill-evals", json={**REPORT, "eval_id": "../x"})
    assert bad_id.status_code == 400
    big = {**REPORT, "per_task": [{"blob": "z" * 2_100_000}]}
    assert (await client.post("/v1/skill-evals", json=big)).status_code == 413
    assert (await client.get("/v1/skill-evals/nope")).status_code == 404


async def test_the_example_says_it_is_one(client: httpx.AsyncClient) -> None:
    for _ in range(2):  # re-seeding is safe
        r = await client.post("/v1/demo/seed-skill-eval")
        assert r.status_code == 200, r.text
    doc = (await client.get("/v1/skill-evals/se-example")).json()
    assert doc["example"] is True
    assert doc["skills"] == ["changelog-entries", "test-first", "house-style"]
    verdicts = {s["skill"]: s["verdict"] for s in doc["per_skill"]}
    assert verdicts["changelog-entries"] == "helps"
    assert verdicts["house-style"].startswith("never opened")
    assert len((await client.get("/v1/skill-evals")).json()["evals"]) == 1
