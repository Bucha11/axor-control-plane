"""Incidents: what needs a look, in one feed.

Four kinds, from what the plane already holds:

- made_up       — a case the audit derived: an agent said a tool worked when it
                  failed, said the tests passed when they failed, sent out text
                  from outside… (runs' evidence);
- dangerous     — a call that was LET THROUGH and should give pause: it deleted
                  or wiped something, or sent out / ran a command built from
                  text that came from outside (step search, step_search.py);
- would_refuse  — let through only because enforcement was off (observe mode):
                  the rules would have refused it;
- refused       — what the rules did refuse: attacks stopped, and normal work
                  blocked by a rule too strict — grouped per agent, tool and day;
- memory_worse  — the team's memory changed in a repository, and the sessions
                  given the new version did significantly worse than those given
                  the one before (memory.compare).

Each item has a status the operator sets — new, looked at, kept as a test, made
a rule of, not a problem; an item whose run was kept as a test reads "kept as a
test" until someone says otherwise.
"""
from __future__ import annotations

from typing import Any

from axor_backend import step_search

KINDS = ("made_up", "dangerous", "would_refuse", "refused", "memory_worse")
STATUSES = ("new", "looked_at", "kept_as_test", "rule_made", "not_a_problem")
# kinds that ask for a look (the menu's count); refusals are the rules working
NEEDS_A_LOOK = ("made_up", "dangerous", "would_refuse", "memory_worse")


def dangerous(step: dict[str, Any]) -> str | None:
    does, outside = step["does"], step["from_outside"]
    if "deletes" in does:
        return "deleted or wiped something"
    if outside and "sends_out" in does:
        return "sent out what came from outside text"
    if outside and "runs_code" in does:
        return "ran a command built from outside text"
    if outside and "changes_files" in does:
        return "changed files as outside text said"
    return None


async def incidents(store: Any, scan: int) -> list[dict[str, Any]]:  # noqa: ANN401
    items: list[dict[str, Any]] = []
    runs = await store.list_runs()
    scenario = {r["run_id"]: r["scenario"] for r in runs}
    for run in runs:
        for i, ev in enumerate(run.get("evidence") or []):
            if not ev.get("deviation"):
                continue
            anchor = ev.get("anchor") or {}
            items.append({
                "id": f"ev:{run['run_id']}:{i}", "kind": "made_up", "ts": run["created_ts"],
                "node_id": run["node_id"], "run_id": run["run_id"],
                "seq": anchor.get("seq"), "anchor_node": anchor.get("node_id"),
                "what": ev["deviation"], "detail": "", "count": 1, "scenario": run["scenario"],
                "case": {"observed_reality": ev.get("observed_reality"),
                         "agent_claim": ev.get("agent_claim"),
                         "fault_attribution": ev.get("fault_attribution") or [],
                         "index": i},
            })
    refused: dict[str, dict[str, Any]] = {}
    for s in step_search.search(await store.recent_tool_lines(scan), limit=10_000):
        if s["kind"] != "tool_call":
            continue
        base = {"ts": s["ts"], "node_id": s["node_id"], "run_id": s["run_id"], "seq": s["seq"],
                "anchor_node": s["node_id"], "detail": s["what"], "count": 1}
        sid = f"step:{s['run_id']}:{s['node_id']}:{s['seq']}"
        if s["outcome"] == "would_refuse":
            items.append({**base, "id": sid, "kind": "would_refuse", "what": s["tool"],
                          "reason": s["reason"]})
        elif s["outcome"] == "let_through" and (why := dangerous(s)):
            items.append({**base, "id": sid, "kind": "dangerous", "what": why,
                          "tool": s["tool"]})
        elif s["outcome"] == "refused":
            day = str(s["ts"] or "")[:10]
            key = f"refused:{s['node_id']}:{s['tool']}:{day}"
            group = refused.get(key)
            if group is None:   # newest first: the first seen is the latest
                refused[key] = {**base, "id": key, "kind": "refused", "what": s["tool"],
                                "reason": s["reason"]}
            else:
                group["count"] += 1
    items += refused.values()
    items += await _memory_worse(store)
    for item in items:
        item.setdefault("scenario", scenario.get(item["run_id"], ""))
    kept = {p["run_id"] for p in await store.pinned()}
    decided = await store.incident_statuses()
    for item in items:
        item["status"] = decided.get(item["id"]) or (
            "kept_as_test" if item["run_id"] in kept and item["kind"] != "refused" else "new")
    items.sort(key=lambda x: str(x["ts"] or ""), reverse=True)
    return items


async def _memory_worse(store: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    """A change of the team's memory after which agents did significantly worse."""
    from axor_backend import memory

    out = []
    for repo, label in (await store.repo_labels()).items():
        for change in await store.memory_changes_of(repo, 5):
            cmp = await memory.compare(store, repo, change)
            if cmp["verdict"] != "worse":
                continue
            what = "; ".join([f"on: {a['text']}" for a in change["added"]]
                             + [f"off: {r['text']}" for r in change["removed"]])
            out.append({"id": f"memory:{repo}:{change['version']}", "kind": "memory_worse",
                        "ts": change["ts"], "node_id": "", "run_id": "", "seq": None,
                        "anchor_node": None, "repo": repo, "label": label,
                        "what": f"agents did worse after the team memory changed in {label}",
                        "detail": what, "count": cmp["after"]["sessions"], "compare": cmp})
    return out
