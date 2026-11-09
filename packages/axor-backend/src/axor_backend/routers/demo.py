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

from axor_backend.clock import now
from axor_backend.deps import StoreDep, SubgraphCacheDep
from axor_backend.storage import Store
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1/demo", tags=["demo"])


async def _seed(
    store: Store, cache: object, run_id: str, node: str, scenario: str,
    events: list[dict], evidence: list[dict],
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
    await store.reseed_run(run_id, node, scenario, now(), events)
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
    from axor_backend import demo, plane

    await seed_tree_run(store, cache)
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
