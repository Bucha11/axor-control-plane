"""Which nodes are the product's own examples (demo seeds), not anyone's work.

Once a workspace's own agents report, the screens stop showing the examples
beside them: example data reads as real data the moment real data is there.
The ids are the ones demo.py seeds; tests/test_examples.py keeps the two in step.
"""
from __future__ import annotations

EXAMPLE_NODES = frozenset({
    "adapter-demo-block", "adapter-demo-pass",
    "tree-orch", "tree-research", "tree-writer", "tree-scraper",
    "support-agent-prod",
})
EXAMPLE_PREFIXES = ("claude-code-example-", "codex-example-", "tree-example-")


def is_example_node(node_id: str) -> bool:
    return (node_id in EXAMPLE_NODES or node_id.startswith(EXAMPLE_PREFIXES)
            or (node_id.startswith("skill-try.") and node_id.endswith(".example")))


async def hidden_examples(store: object) -> set[str]:
    """The examples to leave out: those loaded before the workspace's own agents
    last reported. Work that starts (or resumes) moves the examples aside; an
    example asked for after that shows until the agents report again."""
    reported = await store.all_reported()  # type: ignore[attr-defined]
    nodes = await store.list_nodes()  # type: ignore[attr-defined]
    real = [str((reported.get(n) or {}).get("updated_ts") or "") for n in nodes
            if not is_example_node(n) and not n.startswith("skill-try.")]
    latest = max(real, default="")
    if not latest:
        return set()
    return {n for n in nodes if is_example_node(n)
            and str((reported.get(n) or {}).get("updated_ts") or "") < latest}


async def without_examples(store: object, rows: list[dict], key: str = "node_id") -> list[dict]:
    """``rows`` minus the examples real work has moved aside."""
    hidden = await hidden_examples(store)
    return [r for r in rows if str(r.get(key) or "") not in hidden] if hidden else rows
