"""The example ids the screens hide are exactly the ones the demo seeds."""
from __future__ import annotations

import pathlib

import httpx
from axor_backend import demo
from axor_backend.app import create_app
from axor_backend.clock import now
from axor_backend.examples import is_example_node


def test_every_seeded_node_reads_as_an_example() -> None:
    for node in (demo.EX_BLOCK_NODE, demo.EX_PASS_NODE, demo.TREE_ORCH, demo.TREE_RESEARCH,
                 demo.TREE_WRITER, demo.TREE_SCRAPER, demo.CODING_CLAUDE, demo.CODING_CODEX,
                 demo.CODING_TREE, demo.SKILL_TRY, demo.OPS_AGENT, "claude-code-example-plan"):
        assert is_example_node(node), node


def test_a_real_session_is_not_one() -> None:
    for node in ("claude-code-vm-lena-s1-", "codex-laptop-1a2b3c4d", "support-bot",
                 "skill-try.claude-code.release-helper.k3j2"):
        assert not is_example_node(node), node


async def test_examples_go_for_good_once_real_work_reports(tmp_path: pathlib.Path) -> None:
    """The adapter examples are runs only — no reported row — and go too; a
    reseed or a deleted node does not bring any of them back."""
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/e.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, app.router.lifespan_context(app):
        async def runs() -> set[str]:
            return {r["run_id"] for r in (await c.get("/v1/runs")).json()}

        async def flagged() -> dict[str, bool]:
            nodes = (await c.get("/v1/plane/nodes")).json()
            return {n["node_id"]: n["example_hidden"] for n in nodes if n["example"]}

        assert (await c.post("/v1/demo/seed-adapter-runs")).status_code == 200
        assert (await c.post("/v1/demo/seed-fleet")).status_code == 200
        assert {"ex_block", "ex_pass", "ex_tree"} <= await runs()   # nothing real yet
        assert (await c.get("/v1/workspace")).json()["work_started"] is False
        assert await flagged() and not any((await flagged()).values())

        await app.state.store.upsert_reported("support-bot", applied_version=0, level="NORMAL",
                                              budget_remaining=None, ts=now())
        assert not {"ex_block", "ex_pass", "ex_tree"} & await runs()
        assert all((await flagged()).values())
        assert (await c.get("/v1/workspace")).json()["work_started"] is True

        totals = (await c.get("/v1/plane/fleet")).json()["totals"]
        assert totals["runs"] == 0 and totals["lies"] == 0   # nothing of the examples' counted

        assert (await c.post("/v1/demo/seed-adapter-runs")).status_code == 200
        assert not {"ex_block", "ex_pass"} & await runs()
        assert (await c.get("/v1/runs/ex_block/events")).json()   # its page still opens


async def test_a_demo_plane_keeps_its_examples(tmp_path: pathlib.Path) -> None:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/d.db",
                     operator_keys={}, allow_unsigned=True)
    app.state.store.keep_examples = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, app.router.lifespan_context(app):
        await c.post("/v1/demo/seed-adapter-runs")
        await app.state.store.upsert_reported("support-bot", applied_version=0, level="NORMAL",
                                              budget_remaining=None, ts=now())
        assert {"ex_block", "ex_pass"} <= {r["run_id"] for r in (await c.get("/v1/runs")).json()}


async def test_the_topology_says_which_nodes_are_examples(tmp_path: pathlib.Path) -> None:
    """"Data across sessions" drew the example fleet's reputation with no mark —
    "crm:customers · flagged" read as the workspace's own data."""
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, app.router.lifespan_context(app):
        assert (await c.post("/v1/demo/seed-fleet")).status_code == 200
        nodes = {n["node_id"]: n for n in (await c.get("/v1/plane/topology")).json()["nodes"]}
        assert nodes[demo.TREE_RESEARCH]["example"] is True


async def test_qa174_the_demo_node_is_not_the_workspaces_own_work(tmp_path: pathlib.Path) -> None:
    """QA-174: the first demo node counted as the workspace's own agents
    reporting, so the tree, a second node, the canned tree, the example fleet
    and the example chaos test were no longer offered."""
    from axor_backend.examples import is_real_node

    assert not is_real_node("governed-0a1b2c") and not is_real_node("gov-tree-0a1b-orch")
    assert is_real_node("governed-support-bot")   # a name of the team's own
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/d.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, app.router.lifespan_context(app):
        await app.state.store.upsert_reported("governed-0a1b2c", applied_version=0,
                                              level="NORMAL", budget_remaining=None, ts=now())
        assert (await c.get("/v1/workspace")).json()["work_started"] is False
