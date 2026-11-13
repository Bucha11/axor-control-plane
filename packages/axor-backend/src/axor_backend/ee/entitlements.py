"""What a workspace's plan lets the plane show and operate for coding agents.

The free rung covers one developer and one agent family. Coding sessions past
that are still stored — upgrading shows them with their history — but they are
*locked*: the Agents list names the reason, and an operator command to one is
refused with 402 ``plan_required``. A tree run that mixes agent families (one
writing, another reviewing) is locked the same way.

Who is "the" developer and which family is "the" family: the first to report,
in the order the plane first saw them. The example sessions the demo seeds are
never counted — they exist to show the product, not to use up the free plan.

Protection is not here and never will be: the hooks deny locally whatever the
plan. This only decides what the plane shows and lets you operate.
"""
from __future__ import annotations

from typing import Any

from axor_backend.ee.license import FREE_AGENT_FAMILIES, FREE_DEVELOPERS

EXAMPLE_DEVELOPER_PREFIX = "dev-example-"
REASONS = {
    "developer": "a second developer in this workspace — each needs a seat",
    "agent_family": "a second agent family — mixing Claude Code and Codex is on the paid plan",
}


def workspace_tier(license_: Any | None) -> str:  # noqa: ANN401 - License | None
    return license_.workspace_tier if license_ is not None else "community"


async def coding_entitlements(store: Any, tier: str) -> dict[str, Any]:  # noqa: ANN401
    free = tier == "community"
    allowed_devs: list[str] = []
    allowed_agents: list[str] = []
    locked: dict[str, list[str]] = {}
    for row in await store.coding_profiles():
        dev, agent = row["developer"], row["agent"]
        if dev.startswith(EXAMPLE_DEVELOPER_PREFIX):
            continue
        reasons = []
        if dev not in allowed_devs:
            if not free or len(allowed_devs) < FREE_DEVELOPERS:
                allowed_devs.append(dev)
            else:
                reasons.append("developer")
        if agent and agent not in allowed_agents:
            if not free or len(allowed_agents) < FREE_AGENT_FAMILIES:
                allowed_agents.append(agent)
            else:
                reasons.append("agent_family")
        if reasons:
            locked[row["node_id"]] = reasons
    locked_trees: dict[str, list[str]] = {}
    if free:
        for tree in await store.list_tree_runs(limit=200):
            reasons = []
            if len(tree.get("agent_families") or []) > FREE_AGENT_FAMILIES:
                reasons.append("agent_family")
            if any(m in locked for m in tree.get("members") or []):
                reasons += [r for m in tree["members"] for r in locked.get(m, [])
                            if r not in reasons]
            if reasons and not tree["tree_run"].startswith("tree-example"):
                locked_trees[tree["tree_run"]] = reasons
    return {
        "tier": tier,
        "free": free,
        "developers": FREE_DEVELOPERS if free else None,
        "agent_families": FREE_AGENT_FAMILIES if free else None,
        "agent_family": allowed_agents[0] if free and allowed_agents else None,
        "locked": locked,
        "locked_trees": locked_trees,
        "reasons": REASONS,
    }


def mixes_families(agents: list[str]) -> bool:
    return len({a for a in agents if a}) > FREE_AGENT_FAMILIES
