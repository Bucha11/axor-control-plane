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

import re
from fnmatch import fnmatchcase
from typing import Any

from axor_backend import step_search

KINDS = ("made_up", "dangerous", "would_refuse", "refused", "memory_worse")
STATUSES = ("new", "looked_at", "kept_as_test", "rule_made", "not_a_problem")
# kinds that ask for a look (the menu's count). Refusals too: the rules worked,
# but an attack stopped is still something the operator should see — the fleet
# read "all agents healthy" a minute after one. An operator's own pause / stop /
# budget is a hold, not a refusal, and is not listed at all.
NEEDS_A_LOOK = ("made_up", "dangerous", "would_refuse", "refused", "memory_worse")


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


MUTED = "muted_incidents"
_RULE = re.compile(r"rule '?([a-z_]+)'?")


def cause_of(reason: str) -> tuple[str, str]:
    """Why a call was refused, as one of a few causes: (key, words). The rule's
    id for a rule; else what the kernel or the plane refused it for."""
    text = str(reason or "")
    low = text.lower()
    if "skill_scripts" in low or "inside a script" in low:
        return "skill_scripts", "a skill's script"
    if (m := _RULE.search(text)) and "skill" not in m.group(1):
        return m.group(1), f"rule {m.group(1)}"
    if "skill" in low and "turned off" in low:
        return "skill_off", "a skill turned off"
    if "secret this session read" in low or "confidentiality" in low:
        return "secret_out", "a read secret leaving"
    if "came from" in low or "carrier" in low or "taint" in low or "outside text" in low:
        return "outside_text", "outside text driving it"
    if "stopped from" in low or "paused from" in low:
        return "stopped", "stopped from the Control Plane"
    if "budget" in low:
        return "budget", "the call budget"
    return "other", "other"


def origin_of(reason: str, from_outside: bool) -> str:
    """Who asked for the refused call: a web page, other outside text, a
    skill, or the agent itself (its task, its repository's instructions)."""
    low = str(reason or "").lower()
    if "web page" in low:
        return "web_page"
    if from_outside or "from outside" in low or "outside text" in low:
        return "outside"
    if "skill" in low:
        return "skill"
    return "agent"


def muted(item: dict[str, Any], mutes: list[dict[str, Any]]) -> bool:
    for m in mutes:
        if m.get("kind") == item["kind"] and m.get("what") == item["what"] and fnmatchcase(
                " ".join(str(item.get("detail") or "").split()),
                " ".join(str(m.get("detail") or "").split())):
            return True
    return False


async def incidents(store: Any, scan: int) -> list[dict[str, Any]]:  # noqa: ANN401
    items: list[dict[str, Any]] = []
    runs = await store.list_runs()
    scenario = {r["run_id"]: r["scenario"] for r in runs}
    # The same lie, told again run after run on one day — the payments provider
    # is down and every refund ticket gets "refund issued" — is one item with a
    # count, as a refusal retried is: three identical cards in a row buried the
    # other agents' incidents under one outage. Newest first, so the item is
    # the latest of its kind and its id is that case's (a new one reads new).
    from axor_backend.routers.runs import AUTO_PIN

    # kept by a person, not by the automatic must-block pin (see below): a kept
    # case is its own item, never folded into the ones still to look at
    kept = {p["run_id"] for p in await store.pinned()
            if not str(p.get("label") or "").startswith(AUTO_PIN)}
    made: dict[str, dict[str, Any]] = {}
    for run in sorted(runs, key=lambda r: str(r.get("created_ts") or ""), reverse=True):
        for i, ev in enumerate(run.get("evidence") or []):
            if not ev.get("deviation"):
                continue
            anchor = ev.get("anchor") or {}
            fault = next(iter(ev.get("fault_attribution") or []), {}) or {}
            observed = (ev.get("observed_reality")
                        if isinstance(ev.get("observed_reality"), dict) else {})
            key = ":".join(str(x) for x in (
                run["node_id"], ev["deviation"],
                fault.get("tool_name", ""), fault.get("fault_mode", ""),
                observed.get("observed", ""), str(run.get("created_ts") or "")[:10],
                run["run_id"] in kept))
            if key in made:
                made[key]["count"] += 1
                continue
            made[key] = {
                "id": f"ev:{run['run_id']}:{i}", "kind": "made_up", "ts": run["created_ts"],
                "node_id": run["node_id"], "run_id": run["run_id"],
                "seq": anchor.get("seq"), "anchor_node": anchor.get("node_id"),
                "what": ev["deviation"], "detail": "", "count": 1, "scenario": run["scenario"],
                "case": {"observed_reality": ev.get("observed_reality"),
                         "agent_claim": ev.get("agent_claim"),
                         "fault_attribution": ev.get("fault_attribution") or [],
                         "index": i},
            }
    items += made.values()
    refused: dict[str, dict[str, Any]] = {}
    for s in step_search.search(await store.recent_tool_lines(scan), limit=10_000):
        # a skill's dry run is a try the developer ran on purpose: its story is
        # on Try a skill, not here among the agents' work
        if s["kind"] != "tool_call" or str(s["node_id"]).startswith("skill-try."):
            continue
        base = {"ts": s["ts"], "node_id": s["node_id"], "run_id": s["run_id"], "seq": s["seq"],
                "anchor_node": s["node_id"], "detail": s["what"], "count": 1}
        sid = f"step:{s['run_id']}:{s['node_id']}:{s['seq']}"
        if s["outcome"] == "would_refuse":
            items.append({**base, "id": sid, "kind": "would_refuse", "what": s["tool"],
                          "reason": s["reason"]})
        elif s["outcome"] == "let_through" and not s.get("allowed_by") and (why := dangerous(s)):
            items.append({**base, "id": sid, "kind": "dangerous", "what": why,
                          "tool": s["tool"]})
        elif s["outcome"] == "refused":
            day = str(s["ts"] or "")[:10]
            # an agent retrying the same refused call is one item; a different
            # reason (sending .env out vs a command from a web page) is another,
            # and so is a send to a different destination: a reply refused to the
            # customer and an export refused to a stranger's address were one
            # "send_email ×6", the attack hidden among the false positives
            cause = str(s["reason"] or "").removeprefix("axor: ").split(" — ")[0][:80]
            dest = s.get("dest") or ""
            key = f"refused:{s['node_id']}:{s['tool']}:{day}:{cause}" + (f":{dest}" if dest else "")
            group = refused.get(key)
            if group is None:   # newest first: the first seen is the latest
                cause_key, cause_words = cause_of(s["reason"])
                refused[key] = {**base, "id": key, "kind": "refused", "what": s["tool"],
                                "reason": s["reason"], "cause": cause_key,
                                "cause_words": cause_words, "target": s.get("target") or {},
                                "origin": origin_of(s["reason"], s["from_outside"])}
            else:
                group["count"] += 1
    items += refused.values()
    items += await _memory_worse(store)
    mutes = await store.get_setting(MUTED) if hasattr(store, "get_setting") else None
    if isinstance(mutes, list) and mutes:
        items = [i for i in items if not muted(i, mutes)]
    for item in items:
        item.setdefault("scenario", scenario.get(item["run_id"], ""))
    # kept by a person, not by the automatic must-block pin: a lie caught a
    # moment ago read "kept as a test" — not new, not needing a look, hidden
    # from the default list — before anyone had seen it
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
