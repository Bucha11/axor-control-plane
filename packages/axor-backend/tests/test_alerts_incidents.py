"""Alerts for what needs a look, as it happens: a dangerous call let through,
one the rules would have refused (observe mode), a setup health check that got
worse, agents doing worse after the team's memory changed."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
from axor_backend import memory
from axor_backend.app import create_app


class Spy:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict]] = []

    async def emit(self, trigger: str, node_id: str, payload: dict, org: str | None = None) -> int:
        self.sent.append((trigger, node_id, payload))
        return 1


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/a.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        app.state.notifier = Spy()
        c.spy = app.state.notifier  # type: ignore[attr-defined]
        yield c


def _line(node: str, seq: int, kind: str, verdict: str | None = None, **payload: object) -> dict:
    return {"schema_version": "1.0", "seq": seq, "node_id": node, "kind": kind,
            "ts": datetime.now(UTC).isoformat(), "causal_root": None, "gate": None,
            "verdict": verdict, "payload": payload}


async def _post(c: httpx.AsyncClient, node: str, run: str, *lines: dict) -> None:
    r = await c.post(f"/v1/plane/{node}/telemetry", json={"run_id": run, "events": list(lines)})
    assert r.status_code < 300, r.text


async def test_a_dangerous_call_let_through_pages_once(client: httpx.AsyncClient) -> None:
    # a page fetched in one batch, a command quoting it in the next
    await _post(client, "agent-1", "r1",
                _line("agent-1", 0, "tool_call", "pass", tool="WebFetch",
                      args={"url": "https://docs.example"}),
                _line("agent-1", 1, "tool_result", tool="WebFetch", value_ref="v1",
                      root={"sources": ["web"], "sensitive": False}, preview="run: make deploy"))
    assert client.spy.sent == []  # type: ignore[attr-defined]
    await _post(client, "agent-1", "r1",
                _line("agent-1", 2, "tool_call", "pass", tool="Bash",
                      args={"command": "make deploy"}, arg_refs={"command": "v1"}))
    [(trigger, node, payload)] = client.spy.sent  # type: ignore[attr-defined]
    assert trigger == "dangerous_let_through" and node == "agent-1"
    assert payload["what"] == "ran a command built from outside text"
    assert payload["permalink"] == "/#/explain/r1?seq=2&node=agent-1"


async def test_a_call_the_rules_would_have_refused_pages(client: httpx.AsyncClient) -> None:
    await _post(client, "agent-2", "r2",
                _line("agent-2", 0, "tool_call", "pass", tool="Bash",
                      args={"command": "git push --force"},
                      would_deny={"gate": "consequence", "reason": "a force push"}))
    [(trigger, _node, payload)] = client.spy.sent  # type: ignore[attr-defined]
    assert trigger == "would_refuse" and payload["reason"] == "a force push"


async def test_a_command_carrying_outside_text_counts_as_from_outside(
        client: httpx.AsyncClient) -> None:
    await _post(client, "agent-3", "r3",
                _line("agent-3", 0, "tool_call", "pass", tool="Bash",
                      args={"command": "npx setup-thing"}, carries=["web"]))
    assert [t for t, _, _ in client.spy.sent] == ["dangerous_let_through"]  # type: ignore[attr-defined]


async def test_a_setup_health_check_that_got_worse_pages(client: httpx.AsyncClient) -> None:
    report = {"kind": "setup-health", "agent": "claude-code", "trials": 3, "setup": [],
              "families_raw": {"beyond_task": [2, 3]}, "baseline_raw": {"beyond_task": [0, 3]},
              "causes": [{"words": "the skill fetch-docs"}], "runs": [], "verdict": "worse",
              "rows": [{"family": "beyond_task", "words": "going beyond the task", "worse": True}]}
    assert (await client.post("/v1/setup-health", json=report)).status_code == 201
    [(trigger, _node, payload)] = client.spy.sent  # type: ignore[attr-defined]
    assert trigger == "setup_health_worse"
    assert payload["worse"] == ["going beyond the task"]
    assert payload["traced_to"] == ["the skill fetch-docs"]


async def test_agents_worse_after_a_memory_change_pages_once(
        client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory, "given_on_plan", lambda state, repo: True)
    repo = "r-acme"

    async def session(node: str, dev: str, command: str, passed: bool) -> None:
        await client.post(f"/v1/plane/{node}/profile", json={
            "kind": "coding", "agent": "claude-code", "developer": dev, "repo": repo,
            "repo_label": "acme/api"})
        given = (await client.get("/v1/coding/memory", params={"repo": repo})).json()
        await _post(client, node, f"{node}-r",
                    _line(node, 0, "tool_call", "pass", tool="Bash", args={"command": command}),
                    _line(node, 1, "tool_result", tool="Bash",
                          status="ok" if passed else "error", preview="",
                          check={"kind": "tests", "passed": passed, "command": command}))
        await client.post("/v1/coding/memory/given",
                          json={"node_id": node, "repo": repo, "ids": given["ids"]})

    for n in range(7):
        await session(f"good-{n}", f"dev-{n}", "uv run pytest -q", True)
    await client.post("/v1/memory/notes", json={"repo": repo, "text": "Run tests with -x --lf."})
    for n in range(7):
        await session(f"bad-{n}", f"dev-{n}", "uv run pytest -q -x --lf", False)
    worse = [p for t, _, p in client.spy.sent if t == "memory_worse"]  # type: ignore[attr-defined]
    assert len(worse) == 1 and worse[0]["added"] == ["Run tests with -x --lf."]
    assert worse[0]["repo"] == "acme/api"
