"""The REAL governed tree (spec v2 Ch.4): three IntentLoop nodes, one message
bus — authentic verdicts at every node, labels carried in envelopes, export
denied at the orchestrator. No canned verdicts anywhere."""
from __future__ import annotations

from axor_proxy.governed import run_governed_tree


async def test_tree_produces_three_nodes_and_real_containment() -> None:
    lines, denials, ids = await run_governed_tree("t")

    node_ids = {line["node_id"] for line in lines}
    assert node_ids == set(ids.values())

    # structure from traced spawn events
    spawns = [line for line in lines if line["kind"] == "node_spawned"]
    assert {(s["payload"]["parent_id"], s["payload"]["child_id"]) for s in spawns} == {
        (ids["orch"], ids["research"]), (ids["research"], ids["scraper"]),
    }

    # labels carried over both delegation hops (bus-emitted events)
    received = [line for line in lines if line["kind"] == "message_received"]
    assert len(received) == 2
    assert all(r["payload"]["carried"]["root"]["sources"] == ["web"]
               for r in received)

    # the orchestrator's OWN IntentLoop denied the export — containment
    assert denials == 1
    deny = next(line for line in lines if line.get("verdict") == "deny")
    assert deny["node_id"] == ids["orch"]
    assert deny["payload"]["tool"] == "slack_post"
    assert deny["gate"] == "taint_enforcement"


async def test_tree_events_fold_through_kernel_replay() -> None:
    """Rule 0 end-to-end: the produced multi-node trace folds per node and the
    carried taint denies the export under config re-evaluation."""
    import json

    from axor_core.kernel.events import Verdict, event_from_json_line
    from axor_core.kernel.replay import KernelConfig, replay_tree

    lines, _, ids = await run_governed_tree("r")
    events = [event_from_json_line(json.dumps(line)) for line in lines]
    per_node = replay_tree(events, KernelConfig(
        egress_sinks=frozenset({"slack_post"}),
    ))
    export_steps = [
        s for s in per_node[ids["orch"]].steps
        if s.event.payload.get("tool") == "slack_post"
    ]
    assert export_steps and export_steps[-1].reevaluated_verdict is Verdict.DENY
