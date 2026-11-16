"""Canned runs the proxy cannot produce.

Adapter-depth traces carry recorded verdicts and value provenance; a proxy sees
neither. These two routes seed exactly that depth so counterfactual divergence,
the taint graph and two-sided regression can be demonstrated in-app against real
stored events rather than a mock.

Two things that "seeding real stored events" implies, and neither held.

**A seed must not land in somebody else's run.** The run ids are fixed, and
`ingest_events` is append-only with de-duplication on (node_id, seq) — the demo
nodes are named `tree-*`, so nothing collided and everything was appended. A
tenant with a real run called `ex_tree` got eighteen fabricated governance
events added to it, replayed as one trace:

    events 4 -> 22; node ids now ['prod-node', 'tree-orch', 'tree-research',
                                  'tree-scraper', 'tree-writer']
    GET /v1/replay/ex_tree -> 200, 22 steps over a merged trace

while the run row still said `node='proxy', scenario='custom'`. Seeding now
refuses a run that is not already a demo run, by id and by scenario.

**Re-seeding must actually re-seed.** "Idempotent by construction: re-seeding
overwrites the same run ids" was this module's claim, and de-duplication on
(node_id, seq) is idempotent APPEND, not overwrite: a demo event changed in a
later release, at the same coordinate, was silently ignored and the deployment
kept the older trace forever.

    the shipped demo trace changes in a later release, re-seed:
       the same coordinate is now 'web_search'  ← unchanged
       overwritten: False

`Store.reseed_run` replaces the run outright. It is the one write allowed to do
that, and it is confined to the ids in `demo.DEMO_RUN_IDS`.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from axor_backend.clock import now, today
from axor_backend.deps import StoreDep, SubgraphCacheDep
from axor_backend.storage import Store
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1/demo", tags=["demo"])


async def _seed(
    store: Store, cache: object, run_id: str, node: str, scenario: str,
    events: list[dict], evidence: list[dict], created: str | None = None,
) -> None:
    from axor_backend.demo import DEMO_RUN_IDS, DEMO_SCENARIOS

    if run_id not in DEMO_RUN_IDS:  # pragma: no cover - guards the caller list
        raise HTTPException(500, f"{run_id} is not a demo run id")
    existing = await store.get_run(run_id)
    if existing is not None and existing.get("scenario") not in DEMO_SCENARIOS:
        raise HTTPException(
            409,
            f"run {run_id} already exists in this tenant and is not a demo run "
            f"(scenario {existing.get('scenario')!r}). Seeding would append "
            f"fabricated governance events to a recorded trace; rename or "
            f"delete that run first.",
        )
    await store.reseed_run(run_id, node, scenario, created or now(), events)
    cache.drop_run(current_org_id(), run_id)  # type: ignore[attr-defined]
    if evidence:
        await store.set_evidence(run_id, evidence)


@router.post("/seed-adapter-runs")
async def seed_adapter_runs(
    store: StoreDep, cache: SubgraphCacheDep
) -> dict:
    """Two adapter-fidelity runs: one denied at the boundary, one clean."""
    from axor_backend import demo

    for run_id, node, events, evidence, pin_side in (
        ("ex_block", demo.EX_BLOCK_NODE, demo.EX_BLOCK_EVENTS,
         demo.EX_BLOCK_EVIDENCE, "must_block"),
        ("ex_pass", demo.EX_PASS_NODE, demo.EX_PASS_EVENTS, [], "must_pass"),
    ):
        await _seed(store, cache, run_id, node, "adapter-demo", events, evidence)
        # Pin both corpus sides explicitly (idempotent) — set_evidence's
        # auto-pin lives in the HTTP route, which this seed bypasses. The
        # regression report counts these as canned (corpus.py): they are real
        # replayable evidence about the DEMO, and `safe_to_ship` is a sentence
        # about the deployment's own agents.
        await store.pin(run_id, pin_side, "adapter-demo")
    return {"seeded": ["ex_block", "ex_pass"], "config": demo.EX_CONFIG}


@router.post("/seed-tree-run")
async def seed_tree_run(
    store: StoreDep, cache: SubgraphCacheDep
) -> dict:
    """The canned multi-agent tree run (spec v2): 4 nodes, carried taint up two
    delegation hops, one lateral edge, export denied at the orchestrator — the
    topology graph, the causal subgraph and the two-tree containment story all
    read from this one trace."""
    from axor_backend import demo

    await _seed(store, cache, "ex_tree", demo.TREE_ORCH, "multi-agent-demo",
                demo.TREE_EVENTS, demo.TREE_EVIDENCE)
    return {"seeded": ["ex_tree"], "config": demo.TREE_CONFIG}


@router.post("/seed-fleet")
async def seed_fleet(
    request: Request, store: StoreDep, cache: SubgraphCacheDep
) -> dict:
    """The example fleet: the canned tree on the map, a sentinel reputation
    snapshot on the researcher, and a probe battery with a repair proposal on
    the scraper — what Live agents has to show, without an adapter.

    The tree is the same canned run `seed-tree-run` loads. The snapshot and the
    battery go through the plane's OWN ingest routes, so they are validated,
    stored and broadcast exactly as a real node's would be; they are built by
    axor-sentinel's verdict layer and axor-probe's localizer (`demo.py`).

    Idempotent where it matters: a node that already holds a snapshot or a
    battery keeps it — re-seeding must not pile a second drift report onto a
    node whose operator has since cut the context and re-probed.
    """
    from datetime import UTC, datetime, timedelta

    from axor_backend import demo, plane

    # the canned tree run, stamped now, a second apart: the example reads as a
    # run that just happened ("just now", in order), not the fixture's date
    # with every event at the same midnight
    start = datetime.now(UTC) - timedelta(seconds=len(demo.TREE_EVENTS))
    await _seed(store, cache, "ex_tree", demo.TREE_ORCH, "multi-agent-demo",
                [{**e, "ts": (start + timedelta(seconds=i)).isoformat()}
                 for i, e in enumerate(demo.TREE_EVENTS)],
                demo.TREE_EVIDENCE)
    nodes = [demo.TREE_ORCH, demo.TREE_RESEARCH, demo.TREE_WRITER, demo.TREE_SCRAPER]
    for node in nodes:
        # a heartbeat's worth of reported state, so each node is listed and
        # Health can pick it — the same write a real heartbeat makes
        if await store.get_reported(node) is None:
            await store.upsert_reported(
                node, applied_version=0, level="NORMAL",
                budget_remaining=None, ts=now(),
            )
    seeded: list[str] = ["ex_tree"]
    if await store.reputation(demo.FLEET_SENTINEL_NODE) is None:
        at = time.time()
        await plane.post_reputation(
            demo.FLEET_SENTINEL_NODE, demo.fleet_snapshot(int(at), at), request,
        )
        seeded.append(f"reputation:{demo.FLEET_SENTINEL_NODE}")
    if await store.latest_probe_report(demo.FLEET_PROBE_NODE) is None:
        await plane.post_probe_report(
            demo.FLEET_PROBE_NODE, demo.fleet_battery(drifted=True), request,
        )
        seeded.append(f"probe:{demo.FLEET_PROBE_NODE}")
    return {
        "seeded": seeded, "nodes": nodes,
        "sentinel_node": demo.FLEET_SENTINEL_NODE,
        "probe_node": demo.FLEET_PROBE_NODE,
    }


@router.post("/seed-coding")
async def seed_coding(store: StoreDep, cache: SubgraphCacheDep) -> dict:
    """Example coding agents: a Claude Code and a Codex session in one
    repository (one ledger — Codex is denied running what Claude Code fetched),
    and a tree run with checked findings. Every node is profiled as a coding
    session, so the meter reads developers, not governed nodes."""
    from axor_backend import demo

    # stamped now: the example reads as a session that just ran, not one
    # from the date the fixture was written
    stamp = now()
    for run_id, node, events in (
        ("ex_coding_claude", demo.CODING_CLAUDE, demo.CODING_CLAUDE_EVENTS),
        ("ex_coding_codex", demo.CODING_CODEX, demo.CODING_CODEX_EVENTS),
    ):
        await _seed(store, cache, run_id, node, "coding-demo",
                    [{**e, "ts": stamp} for e in events], [])
    sessions = [demo.CODING_CLAUDE, demo.CODING_CODEX, *demo.CODING_TREE_NODES]
    ts = now()
    for node in [*sessions, demo.CODING_TREE]:
        if await store.get_reported(node) is None:
            await store.upsert_reported(node, applied_version=0, level="NORMAL",
                                        budget_remaining=None, ts=ts)
    for node in sessions:
        agent = "codex" if node.startswith("codex-") else "claude-code"
        developer = demo.CODING_DEVELOPERS.get(node, "dev-example-ada")
        await store.put_node_profile(node, "coding", agent, developer, ts)
        # reported today, as a real session's heartbeat records — the seat
        # meter counts developers whose agents were active
        await store.record_node_activity(node, today())
    await store.put_node_profile(demo.CODING_TREE, "tree", "", "", ts)
    snapshot = demo.coding_tree_snapshot()
    await store.put_tree_run(demo.CODING_TREE, snapshot.pop("status"), snapshot, ts)
    return {"sessions": [demo.CODING_CLAUDE, demo.CODING_CODEX],
            "tree_run": demo.CODING_TREE, "runs": ["ex_coding_claude", "ex_coding_codex"]}


@router.post("/seed-skill-try")
async def seed_skill_try(store: StoreDep, cache: SubgraphCacheDep) -> dict:
    """An example `axor-wrap skill try`: one Claude Code session with a skill,
    enforcement off, one call marked as what Axor would have refused."""
    from axor_backend import demo

    stamp = now()
    await _seed(store, cache, "ex_skill_try", demo.SKILL_TRY, "skill-demo",
                [{**e, "ts": stamp} for e in demo.SKILL_TRY_EVENTS], [])
    if await store.get_reported(demo.SKILL_TRY) is None:
        await store.upsert_reported(demo.SKILL_TRY, applied_version=0, level="NORMAL",
                                    budget_remaining=None, ts=stamp)
    await store.put_node_profile(demo.SKILL_TRY, "coding", "claude-code", "dev-example-ada", stamp)
    return {"node": demo.SKILL_TRY, "run": "ex_skill_try"}


@router.post("/seed-skill-eval")
async def seed_skill_eval(store: StoreDep) -> dict:
    """An example skill eval: three skills on one task (axor-eval's
    experiments/skills/changelog), 8 arms × 8 trials, as `--plane` posts it.
    The numbers come from a scripted agent, and the example says so."""
    from axor_backend import demo

    doc = {**demo.skill_eval_example(), "example": True}
    await store.put_skill_eval("se-example", doc, now())
    return {"eval_id": "se-example"}


@router.post("/fleet-reprobe", status_code=201)
async def fleet_reprobe(request: Request, store: StoreDep) -> dict:
    """Play the example scraper's half of a heal: take the cut it was sent,
    then run the verifying re-probe.

    A real node does both itself — its adapter consumes `pending_excision` at
    the next intent boundary, and its own battery is the re-probe. The example
    node has no runtime, so without this a heal on it waited forever. It does
    only that: refused (409) unless a cut is actually pending on the example
    scraper, so it cannot clear a drift nobody cut, and it never touches any
    other node.
    """
    from axor_backend import demo, plane

    node = demo.FLEET_PROBE_NODE
    if not await store.open_heal_attempts(node):
        raise HTTPException(
            409,
            f"no cut is waiting on {node}: send the excision first, then re-probe",
        )
    await store.clear_desired_key(node, "pending_excision")
    return await plane.post_probe_report(node, demo.fleet_battery(drifted=False), request)


@router.post("/seed-prod-incident")
async def seed_prod_incident(store: StoreDep, cache: SubgraphCacheDep) -> dict:
    """Example for step search: last Thursday afternoon a support agent dropped
    the production orders table because a ticket told it to — let through; and
    the day before, the same agent's normal work (an order looked up and the
    customer answered), which a rule against the drop must leave passing."""
    from datetime import UTC, datetime, timedelta

    from axor_backend import demo

    today = datetime.now(UTC).replace(hour=14, minute=31, second=0, microsecond=0)
    back = (today.weekday() - 3) % 7 or 7          # the last Thursday before today
    start = today - timedelta(days=back)
    await _seed(store, cache, "ex_prod_drop", demo.OPS_AGENT, "ops-demo",
                [{**e, "ts": (start + timedelta(seconds=9 * i)).isoformat()}
                 for i, e in enumerate(demo.OPS_EVENTS)], [], created=start.isoformat())
    day_before = start - timedelta(days=1, hours=3)
    await _seed(store, cache, "ex_prod_ok", demo.OPS_AGENT, "ops-demo",
                [{**e, "ts": (day_before + timedelta(seconds=7 * i)).isoformat()}
                 for i, e in enumerate(demo.OPS_OK_EVENTS)], [], created=day_before.isoformat())
    if await store.get_reported(demo.OPS_AGENT) is None:
        await store.upsert_reported(demo.OPS_AGENT, applied_version=0, level="NORMAL",
                                    budget_remaining=None, ts=now())
    return {"seeded": ["ex_prod_drop", "ex_prod_ok"], "node": demo.OPS_AGENT,
            "at": start.isoformat()}
