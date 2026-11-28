"""The team's memory over time: rotation keeps what agents get true (dormant,
replaced, a fix following its command, suggestions dropped), every change of
what agents get is logged with a version, and a change after which the
sessions did significantly worse is a signal — on the memory screen and in
Incidents."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from axor_backend import memory
from axor_backend.app import create_app

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
REPO = "r-acme-api"


def _ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat()


def _item(i: int, status: str, command: str, *, kind: str = "runs", check: str = "tests",
          first: float = 10, last: float = 1, **doc: object) -> dict:
    return {"id": i, "kind": kind, "status": status, "updated_ts": _ago(last),
            "doc": {"check_kind": check, "command": command, "text": f"{check}: `{command}`",
                    "first_ok_ts": _ago(first), "last_ok_ts": _ago(last), **doc}}


def test_not_confirmed_for_fresh_days_goes_dormant_and_a_person_can_vouch() -> None:
    items = [_item(1, "on", "uv run pytest -q", last=31),
             _item(2, "on", "make lint", check="lint", last=29)]
    changes, drop = memory.plan_rotation(items, NOW, 30)
    assert changes[1][0] == "dormant" and 2 not in changes and drop == []
    vouched = [_item(1, "dormant", "uv run pytest -q", last=40, vouched_ts=_ago(2))]
    assert memory.plan_rotation(vouched, NOW, 30)[0][1][0] == "on"


def test_replaced_when_the_team_moved_on_kept_when_both_are_in_use() -> None:
    old = _item(1, "on", "uv run pytest -q", first=20, last=6)
    new = _item(2, "on", "make test", first=5, last=1)
    changes, _ = memory.plan_rotation([old, new], NOW, 30)
    assert changes[1][0] == "replaced" and changes[1][1]["replaced_by"] == "tests: `make test`"
    # both still passing after each other: two lint commands, both kept
    a = _item(3, "on", "ruff check .", check="lint", first=20, last=1)
    b = _item(4, "on", "ruff format --check", check="lint", first=5, last=2)
    assert memory.plan_rotation([a, b], NOW, 30)[0] == {}
    # a person turned the replaced one back on: kept
    kept = _item(1, "on", "uv run pytest -q", first=20, last=6, keep=True)
    assert 1 not in memory.plan_rotation([kept, new], NOW, 30)[0]


def test_a_fix_follows_the_command_it_says_works() -> None:
    works = _item(1, "on", "uv run pytest -q", first=20, last=6)
    new = _item(2, "on", "make test", first=5, last=1)
    fix = _item(3, "on", "uv run pytest -q", kind="fix", first=20, last=6, failed="pytest -q")
    changes, _ = memory.plan_rotation([works, new, fix], NOW, 30)
    assert changes[3][0] == "replaced"


def test_a_suggestion_nobody_confirmed_is_dropped() -> None:
    items = [_item(1, "suggested", "make docs", check="build", last=15),
             _item(2, "suggested", "make lint", check="lint", last=3)]
    assert memory.plan_rotation(items, NOW, 30)[1] == [1]


def test_fisher() -> None:
    assert memory.fisher_two_sided(1, 9, 8, 2) < 0.01
    assert memory.fisher_two_sided(3, 7, 4, 6) > 0.5


# ── through the API ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _team_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about what agents get: a workspace on a paid plan."""
    monkeypatch.setattr(memory, "given_on_plan", lambda state, repo: True)

@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/d.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def _session(c: httpx.AsyncClient, node: str, dev: str, command: str | None,
                   passed: bool = True) -> None:
    await c.post(f"/v1/plane/{node}/profile", json={
        "kind": "coding", "agent": "claude-code", "developer": dev, "repo": REPO,
        "repo_label": "acme/api"})
    given = (await c.get("/v1/coding/memory", params={"repo": REPO})).json()
    await c.post("/v1/coding/memory/given", json={"node_id": node, "repo": REPO,
                                                  "ids": given["ids"]})
    if command is None:
        return
    now = datetime.now(UTC).isoformat()
    r = await c.post(f"/v1/plane/{node}/telemetry", json={"run_id": f"{node}-r", "events": [
        {"schema_version": "1.0", "seq": 0, "node_id": node, "kind": "tool_call", "ts": now,
         "causal_root": None, "gate": None, "verdict": "pass",
         "payload": {"tool": "Bash", "args": {"command": command}}},
        {"schema_version": "1.0", "seq": 1, "node_id": node, "kind": "tool_result", "ts": now,
         "causal_root": None, "gate": None, "verdict": None,
         "payload": {"tool": "Bash", "status": "ok" if passed else "error", "preview": "",
                     "check": {"kind": "tests", "passed": passed, "command": command,
                               "summary": ""}}}]})
    assert r.status_code < 300, r.text


async def test_a_change_after_which_sessions_did_worse_is_a_signal(
        client: httpx.AsyncClient) -> None:
    # two developers confirm the tests command: on by itself — version 1
    await _session(client, "s-ada", "dev-ada", "uv run pytest -q")
    await _session(client, "s-bob", "dev-bob", "uv run pytest -q")
    v1 = (await client.get("/v1/coding/memory", params={"repo": REPO})).json()
    assert v1["version"] and v1["ids"]
    # each fact on its own, for a setup health check to take out one by one
    assert v1["items"] == [{"id": v1["ids"][0], "text": "tests: `uv run pytest -q`",
                            "kind": "runs"}] and v1["label"] == "acme/api"
    # six sessions given version 1: their first check passes
    for n in range(6):
        await _session(client, f"good-{n}", f"dev-{n}", "uv run pytest -q")
    # someone adds a note (a bad one) — version 2
    await client.post("/v1/memory/notes", json={"repo": REPO,
                                                "text": "Always run the tests with -x --lf."})
    v2 = (await client.get("/v1/coding/memory", params={"repo": REPO})).json()
    assert v2["version"] != v1["version"]
    # six sessions given version 2: their first check fails
    for n in range(6):
        await _session(client, f"bad-{n}", f"dev-{n}", "uv run pytest -q -x --lf", passed=False)
    repo = next(r for r in (await client.get("/v1/memory")).json()["repos"] if r["repo"] == REPO)
    latest = repo["changes"][0]
    assert latest["version"] == v2["version"] and latest["previous"] == v1["version"]
    assert [a["text"] for a in latest["added"]] == ["Always run the tests with -x --lf."]
    cmp = latest["compare"]
    assert cmp["verdict"] == "worse" and cmp["after"]["first_check_failed"] == 6
    assert cmp["before"]["sessions"] >= 6
    assert repo["drift"]["version"] == v2["version"]
    feed = (await client.get("/v1/incidents")).json()
    worse = [i for i in feed["items"] if i["kind"] == "memory_worse"]
    assert len(worse) == 1 and "acme/api" in worse[0]["what"]
    assert "Always run the tests" in worse[0]["detail"]
    # turning off what the change added settles the drift; the incident stays on the record
    note_id = latest["added"][0]["id"]
    await client.put(f"/v1/memory/{note_id}", json={"status": "off"})
    repo = next(r for r in (await client.get("/v1/memory")).json()["repos"] if r["repo"] == REPO)
    assert repo["drift"] is None
    # what one session was given, for Replay
    given = (await client.get("/v1/memory/session/bad-0")).json()["given"]
    assert "Always run the tests with -x --lf." in [i["text"] for i in given[0]["items"]]


async def test_too_few_sessions_says_so(client: httpx.AsyncClient) -> None:
    await _session(client, "s-ada", "dev-ada", "uv run pytest -q")
    await _session(client, "s-bob", "dev-bob", "uv run pytest -q")
    await _session(client, "s-cat", "dev-cat", "uv run pytest -q")
    repo = next(r for r in (await client.get("/v1/memory")).json()["repos"] if r["repo"] == REPO)
    assert repo["changes"][0]["compare"]["verdict"] == "too_few" and repo["drift"] is None


async def test_retention_drops_evidence_not_what_was_learned(client: httpx.AsyncClient) -> None:
    await _session(client, "s-ada", "dev-ada", "uv run pytest -q")
    app_store = client._transport.app.state.store  # type: ignore[attr-defined]
    pruned = await app_store.prune_runs_older_than("2999-01-01T00:00:00+00:00")
    assert pruned >= 1
    item = next(i for i in (await client.get("/v1/memory")).json()["repos"][0]["items"])
    assert item["doc"]["evidence"] == [] and item["doc"]["evidence_pruned"] == 1
    assert item["doc"]["text"] == "tests: `uv run pytest -q`"


async def test_fresh_days_per_workspace(client: httpx.AsyncClient) -> None:
    assert (await client.put("/v1/memory/settings", json={"fresh_days": 7})).status_code == 200
    assert (await client.get("/v1/memory")).json()["fresh_days"] == 7
    assert (await client.put("/v1/memory/settings", json={"fresh_days": 0})).status_code == 400


# ── a check that passes without the code passing it ─────────────────────────

@pytest.mark.parametrize("command,why", [
    ("pnpm test -u", "updates snapshots"),
    ("npx jest -u src/", "updates snapshots"),
    ("pnpm vitest run -u", "updates snapshots"),
    ("npm test -- --updateSnapshot", "updates snapshots"),
    ("pytest --snapshot-update", "updates snapshots"),
    ("UPDATE_SNAPSHOTS=1 make test", "updates snapshots"),
    ("pytest -q || true", "swallows the failure"),
    ("make test; exit 0", "swallows the failure"),
    ("ruff check --exit-zero .", "swallows the failure"),
    ("pytest --deselect tests/test_login.py::test_lockout", "leaves tests out"),
    ("pytest -k 'not lockout'", "leaves tests out"),
    ("pytest -k \"not slow and not lockout\"", "leaves tests out"),
    ("pytest --ignore=tests/integration", "leaves tests out"),
    ("jest --passWithNoTests", "leaves tests out"),
    ("pytest --collect-only -q", "leaves tests out"),
    ("cargo test -- --skip lockout", "leaves tests out"),
])
def test_a_check_that_passes_without_the_code_passing_it_is_kept_out(command: str,
                                                                     why: str) -> None:
    reason = memory.kept_out(command, None)
    assert reason and why in reason and "never handed on" in reason


@pytest.mark.parametrize("command", [
    "pnpm test", "uv run pytest -q", "pytest -q -x --lf", "python -u -m pytest",
    "pytest -k login", "npm test -- --runInBand", "go test ./...", "ruff check --fix .",
    "cargo test", "make typecheck", "pytest tests/test_unit.py",
])
def test_ordinary_checks_are_not(command: str) -> None:
    assert memory.kept_out(command, None) is None


def test_a_filter_added_later_applies_to_what_was_already_learned() -> None:
    items = [_item(1, "on", "pnpm test -u", last=1), _item(2, "on", "pnpm test", last=1),
             _item(3, "suggested", "pytest -q || true", last=1)]
    changes, _ = memory.plan_rotation(items, NOW, 30)
    assert changes[1][0] == "kept_out" and "snapshots" in changes[1][1]["reason"]
    assert changes[3][0] == "kept_out" and 2 not in changes


async def test_turned_off_counts_only_its_own_sessions_and_says_who(
        client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """QA-503: two sessions ran before anything was given; turned on, then off
    a minute later. The off row read "0 before, 2 after" — "" before the
    first change and "" after the off are the same version — and did not say
    who turned it off."""
    from axor_backend.routers import memory_api

    monkeypatch.setattr(memory_api, "_who", lambda principal: "lee@acme.io")
    await _session(client, "s-ada", "dev-ada", "uv run pytest -q")
    await _session(client, "s-ada2", "dev-ada", None)
    item = next(i for i in (await client.get("/v1/memory")).json()["repos"][0]["items"]
                if i["kind"] == "runs")
    assert (await client.put(f"/v1/memory/{item['id']}", json={"status": "on"})).status_code == 200
    assert (await client.put(f"/v1/memory/{item['id']}", json={"status": "off"})).status_code == 200
    repo = next(r for r in (await client.get("/v1/memory")).json()["repos"] if r["repo"] == REPO)
    off, on = repo["changes"][0], repo["changes"][1]
    assert on["added"] and on["compare"]["before"]["sessions"] == 2
    assert on["compare"]["after"]["sessions"] == 0
    assert off["removed"] and off["compare"]["before"]["sessions"] == 0
    assert off["compare"]["after"]["sessions"] == 0
    assert off["removed"][0]["why"] == "turned off by lee@acme.io"
