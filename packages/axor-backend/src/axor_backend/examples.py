"""Which nodes are the product's own examples (demo seeds), not anyone's work.

Once a workspace's own agents first report, the screens stop showing the
examples for good: example data reads as real data the moment real data is
there. A run page opened by its id still opens.
The ids are the ones demo.py seeds; tests/test_examples.py keeps the two in step.
"""
from __future__ import annotations

import re

EXAMPLE_NODES = frozenset({
    "adapter-demo-block", "adapter-demo-pass",
    "tree-orch", "tree-research", "tree-writer", "tree-scraper",
    "support-agent-prod",
})
EXAMPLE_PREFIXES = ("claude-code-example-", "codex-example-", "tree-example-")


def is_example_node(node_id: str) -> bool:
    return (node_id in EXAMPLE_NODES or node_id.startswith(EXAMPLE_PREFIXES)
            or (node_id.startswith("skill-try.") and node_id.endswith(".example")))


WORK_STARTED = "workspace_work_started"


# The proxy's demo node and demo tree (axor_proxy.governed): real IntentLoops
# that take commands, so not examples to hide, but the product showing itself,
# not the team at work. Counting the first one as work hid every other example
# the moment it heartbeat — the tree, a second node, the fleet (QA-174).
_DEMO_NODE = re.compile(r"governed-[0-9a-f]{6}|gov-tree-[0-9a-f]{4}-(orch|research|scraper)")


def is_real_node(node_id: str) -> bool:
    # a skill dry run is the product trying a skill, not the team's agents at work
    return (not is_example_node(node_id) and not node_id.startswith("skill-try.")
            and not _DEMO_NODE.fullmatch(node_id))


async def work_started(store: object) -> str | None:
    """When the workspace's own agents first reported, or None if they have not.

    Written once, when a real node first reports (``Store.upsert_reported``),
    and kept: the examples do not come back when a node is deleted or an
    example is reseeded. A workspace whose agents reported before the flag
    existed gets it on the first look."""
    if getattr(store, "keep_examples", False):
        return None   # a demo plane (AXOR_KEEP_EXAMPLES): the examples stay
    since = await store.get_setting(WORK_STARTED)  # type: ignore[attr-defined]
    if since:
        return str(since)
    reported = await store.all_reported()  # type: ignore[attr-defined]
    real = sorted(str((r or {}).get("updated_ts") or "")
                  for n, r in reported.items() if is_real_node(n))
    if not real:
        return None
    await store.set_setting(WORK_STARTED, real[0] or "unknown")  # type: ignore[attr-defined]
    return real[0] or "unknown"


async def without_examples(store: object, rows: list[dict], key: str = "node_id") -> list[dict]:
    """``rows`` minus the product's examples, once the workspace's own work started."""
    if not await work_started(store):
        return rows
    return [r for r in rows if not is_example_node(str(r.get(key) or ""))]
