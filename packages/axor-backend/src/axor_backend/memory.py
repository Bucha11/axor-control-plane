"""The team's memory: what coding agents verifiably learned in a repository,
given to every agent of the team at the start of its next session there.

Only what the sidecars RECORDED counts — never what an agent said it learned:

- ``runs``: a check command (tests, lint, typecheck, build) that passed —
  "tests: `uv run pytest -q`";
- ``fix``: in one session, a check of one kind failed and then passed with a
  different command — "`pytest` fails here — `uv run pytest -q` works";
- ``note``: something a person on the team wrote, taken as their own words.

What is kept out, whatever passed: a command whose arguments carry text from
outside (the sidecar's ledger says which: web, mcp, a note from outside the
team), anything the rules would never let run unattended (sending out,
``curl | sh``, a push, a recursive delete), anything shaped like a secret,
anything long — and a check that passes without the code passing it: one that
rewrites the expected output (``jest -u``, ``--snapshot-update``), swallows
the failure (``|| true``, ``--exit-zero``), or leaves the failing tests out
(``--deselect``, ``-k "not …"``, ``--passWithNoTests``, ``--collect-only``).
Remembered, that would teach every agent of the team to make a red check
green without fixing anything. A kept-out command is listed — "kept out: it came from a
web page" — and never given to an agent.

An item found automatically is suggested; one click turns it on, and it turns
on by itself once it passed for two different developers.

Rotation (``rotate``), so what agents get stays true:

- stopped working — since it last passed, it failed for two developers, or
  three times for one (one developer's broken machine does not take it from
  the team);
- not confirmed — no pass for ``fresh_days`` (30 by default, per workspace):
  dormant, not given; passing again brings it back;
- replaced — a newer command of the same kind started passing and this one
  has not passed since: the team moved on (two lint commands both still in
  use are both kept);
- a suggestion nobody confirmed in 14 days is dropped;
- people's notes do not expire; past 90 days the screen asks "still true?".

Drift (``refresh`` / ``compare``): what agents get has a version — a hash of
the items in it. Every change of version is logged with what changed and why,
and the sessions given the version before are compared with those given the
new one, on what the plane already recorded: did the first check pass, did the
agent say the checks passed when they failed. Significantly worse after a
change (Fisher's exact test) is a signal on the memory screen, in context
health and in Incidents.
"""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

KINDS = ("runs", "fix", "note")
GIVEN = "on"
STATUSES = ("suggested", "on", "off", "stale", "dormant", "replaced", "kept_out")
AUTO_ON_DEVELOPERS = 2
STALE_DEVELOPERS = 2          # failed for this many developers since its last pass…
STALE_ONE_DEVELOPER = 3       # …or this many times for one
FRESH_DAYS = 30
FRESH_SETTING = "memory_fresh_days"
SUGGESTED_DAYS = 14
NOTE_ASK_DAYS = 90
MIN_SESSIONS = 5              # per side, before a comparison says anything
# agents are given the team's memory on this workspace tier and above — Coding,
# the plan for a team of developers (Team and up include everything in it).
# Below it — one developer, free — the plane keeps learning (an upgrade gives
# agents what was learned) and gives nothing. The example repository is always
# given: it shows the product.
PLAN_TIER = "coding"
WORSE_P = 0.05
MAX_COMMAND = 200
TEXT_LIMIT = 1500
EVIDENCE_KEEP = 10

_DANGER = re.compile(
    r"\b(curl|wget|ssh|scp|rsync|sudo|nc|ncat|telnet|kubectl)\b"
    r"|git\s+push|rm\s+-[a-zA-Z]*r|npm\s+publish|twine\s+upload|docker\s+push"
    r"|terraform\s+(apply|destroy)|\|\s*(sh|bash|zsh)\b")
_SECRET = re.compile(
    r"(?i)(api[_-]?key|token|secret|passw(or)?d|bearer)\s*[=:]"
    r"|sk_(live|test)_|ghp_[A-Za-z0-9]{8}|AKIA[0-9A-Z]{12}|xox[abp]-")
_OUTSIDE_WORDS = {
    "web": "a web page", "mcp": "an MCP tool", "unknown_external": "a note from outside the team",
    "child_agent": "another agent's output", "api": "an API's answer",
    "file": "a file from outside",
}


# a check that passes without the code passing it — (pattern, what it does)
_GAMES = (
    (re.compile(r"(?:\b(?:jest|vitest)\b|(?:^|\s)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test\b)"
                r"[^|;&]*\s-u\b"
                r"|--update-?snapshots?\b|--updateSnapshot\b|--snapshot-update\b"
                r"|\bUPDATE_SNAPSHOTS?=\S", re.I),
     "it rewrites the expected output (updates snapshots) instead of passing it"),
    (re.compile(r"\|\|\s*(?:true|:|exit\s+0)\b|;\s*(?:true|exit\s+0)\s*$|\bset\s+\+e\b"
                r"|--exit-zero\b|--continue-on-error\b|\bcontinue-on-error\b"),
     "it swallows the failure, so it passes whatever happened"),
    (re.compile(r"--deselect\b|-k\s+['\"]?\s*not\b|--ignore(?:-glob)?=|--skip\b"
                r"|--testPathIgnorePatterns\b|--pass-?[wW]ith-?[nN]o-?[tT]ests\b"
                r"|--co\b|--collect-only\b|--no-tests\b"),
     "it leaves tests out (deselects, skips, or only collects them)"),
)


def games_the_check(command: str) -> str | None:
    """Why a check command passes without the code passing it, or None."""
    for pattern, why in _GAMES:
        if pattern.search(command):
            return why
    return None


def kept_out(command: str, carries: list[str] | None) -> str | None:
    """Why a command is never remembered, or None."""
    if carries:
        where = ", ".join(_OUTSIDE_WORDS.get(c, c) for c in carries)
        return f"it came from {where} — Axor never remembers a command something outside wrote"
    if len(command) > MAX_COMMAND:
        return "too long to be a how-to"
    if _SECRET.search(command):
        return "it looks like it holds a secret"
    if _DANGER.search(command):
        return "it sends something out, deletes or publishes — never handed to an agent as a how-to"
    if why := games_the_check(command):
        return f"{why} — never handed on as how to run the check"
    return None


def _text(kind: str, check_kind: str, command: str, failed: str = "") -> str:
    if kind == "fix":
        return f"{check_kind}: `{failed}` fails here — `{command}` works"
    return f"{check_kind}: `{command}`"


async def _bump(store: Any, repo: str, key: str, kind: str, base: dict, *,  # noqa: ANN401
                passed: bool, developer: str, evidence: dict, ts: str,
                reason: str | None = None) -> None:
    """One observation of one item: create it, or move it along."""
    item = await store.memory_item(repo, key)
    if item is None:
        if not passed:
            return  # a failure alone teaches nothing to remember
        doc = {**base, "developers": [developer] if developer else [], "evidence": [evidence],
               "fails": 0, "fail_devs": {}, "first_ok_ts": ts, "last_ok_ts": ts, "given": 0,
               "auto": False, "reason": reason or ""}
        await store.add_memory_item(repo, key, kind, "kept_out" if reason else "suggested",
                                    doc, ts)
        return
    doc, status = item["doc"], item["status"]
    if passed:
        if developer and developer not in doc["developers"]:
            doc["developers"].append(developer)
        doc["evidence"] = ([evidence] + doc["evidence"])[:EVIDENCE_KEEP]
        doc["fails"], doc["fail_devs"], doc["last_ok_ts"] = 0, {}, ts
        doc.setdefault("first_ok_ts", ts)
        if status == "suggested" and len(doc["developers"]) >= AUTO_ON_DEVELOPERS:
            status, doc["auto"] = "on", True
        elif status in ("stale", "dormant", "replaced"):
            status = "on"          # works again; rotate() decides what is given
    else:
        doc["fails"] = doc.get("fails", 0) + 1
        devs = dict(doc.get("fail_devs") or {})
        who = developer or "?"
        devs[who] = devs.get(who, 0) + 1
        doc["fail_devs"], doc["last_fail_ts"] = devs, ts
        if status in ("on", "replaced", "dormant") and (
                len(devs) >= STALE_DEVELOPERS or max(devs.values()) >= STALE_ONE_DEVELOPER):
            status = "stale"
    await store.update_memory_item(item["id"], status, doc, ts)


async def observe(store: Any, node_id: str, run_id: str, lines: list[dict]) -> int:  # noqa: ANN401
    """Fold a batch's check results into the repository's memory. Returns how
    many it looked at."""
    checks = [ln for ln in lines if ln.get("kind") == "tool_result"
              and isinstance((ln.get("payload") or {}).get("check"), dict)
              and (ln["payload"]["check"].get("command"))]
    if not checks:
        return 0
    profile = (await store.node_kinds()).get(node_id) or {}
    repo = profile.get("repo") or ""
    if profile.get("kind") != "coding" or not repo:
        return 0
    developer = profile.get("developer", "")
    earlier: list[dict] | None = None
    for ln in checks:
        p = ln["payload"]
        check = p["check"]
        command = str(check["command"]).strip()
        ck = str(check.get("kind") or "check")
        passed = bool(check.get("passed"))
        ts = str(ln.get("ts") or "")
        evidence = {"run_id": run_id, "node_id": node_id, "seq": ln.get("seq"),
                    "developer": developer, "ts": ts,
                    "summary": str(check.get("summary") or "")[:200]}
        reason = kept_out(command, p.get("carries"))
        base = {"check_kind": ck, "command": command, "text": _text("runs", ck, command)}
        await _bump(store, repo, f"runs:{ck}:{command}", "runs", base, passed=passed,
                    developer=developer, evidence=evidence, ts=ts, reason=reason)
        if not passed or reason:
            continue
        # failed, then passed with another command: the fix is what to remember
        if earlier is None:
            earlier = await store.run_events(run_id)
        seq = ln.get("seq")
        failed = next((e for e in reversed(earlier)
                       if e.get("kind") == "tool_result" and e.get("seq") != seq
                       and (e.get("seq") or 0) < (seq or 0)
                       and isinstance((e.get("payload") or {}).get("check"), dict)
                       and e["payload"]["check"].get("kind") == ck
                       and not e["payload"]["check"].get("passed")), None)
        if failed is None:
            continue
        bad = str(failed["payload"]["check"].get("command") or "").strip()
        if not bad or bad == command or kept_out(bad, failed["payload"].get("carries")):
            continue
        fix = {"check_kind": ck, "command": command, "failed": bad,
               "text": _text("fix", ck, command, bad)}
        await _bump(store, repo, f"fix:{ck}:{bad}->{command}", "fix", fix, passed=True,
                    developer=developer, evidence=evidence, ts=ts)
    return len(checks)


def for_agent(items: list[dict], label: str) -> tuple[str, list[int]]:
    """The text an agent is given, and which items it holds — what is on, the
    team's notes first, then fixes, then how to run, newest first, within
    TEXT_LIMIT."""
    on = [i for i in items if i["status"] == GIVEN]
    if not on:
        return "", []
    order = {"note": 0, "fix": 1, "runs": 2}
    notes = [i for i in on if i["kind"] == "note"]
    learned = sorted((i for i in on if i["kind"] != "note"),
                     key=lambda i: (order[i["kind"]], -_stamp(i)))
    head = (f"What your team's coding agents verifiably learned in {label or 'this repository'} "
            "(each from commands that passed in their sessions — Axor Control Plane):")
    out, ids = [head], []
    size = len(head)
    for i in learned:
        line = f"- {i['doc']['text']}"
        if size + len(line) + 1 > TEXT_LIMIT:
            break
        out.append(line)
        ids.append(i["id"])
        size += len(line) + 1
    if notes:
        title = "From people on the team:"
        out.append(title)
        size += len(title) + 1
        for i in notes:
            line = f"- {i['doc']['text']}"
            if size + len(line) + 1 > TEXT_LIMIT:
                break
            out.append(line)
            ids.append(i["id"])
            size += len(line) + 1
    if not ids:
        return "", []
    return "\n".join(out), ids


def _stamp(item: dict) -> float:
    from datetime import datetime

    ts = item["doc"].get("last_ok_ts") or item["updated_ts"] or ""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ── rotation ────────────────────────────────────────────────────────────────

def _ts(value: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def plan_rotation(items: list[dict], now: datetime, fresh_days: int) -> tuple[
        dict[int, tuple[str, dict]], list[int]]:
    """What rotation changes: ``{id: (status, doc)}``, and the suggestions to
    drop. Pure, so the rules are tested apart from the store."""
    fresh = now - timedelta(days=fresh_days)
    drop = [i["id"] for i in items if i["status"] == "suggested"
            and (_ts(i["doc"].get("last_ok_ts")) or now) < now - timedelta(days=SUGGESTED_DAYS)]
    want: dict[int, tuple[str, dict]] = {}
    # a filter added later applies to what was learned before it
    for i in items:
        if i["kind"] in ("runs", "fix") and i["status"] in ("suggested", "on", "replaced",
                                                           "dormant", "stale", "off"):
            why = kept_out(str(i["doc"].get("command") or ""), None)
            if why:
                want[i["id"]] = ("kept_out", {"reason": why})
    out_now = set(want)
    live = [i for i in items if i["id"] not in out_now
            and i["status"] in ("on", "replaced", "dormant") and i["kind"] != "note"]
    for i in live:
        # a person who turned it on vouched for it then, as a pass would
        stamps = [t for t in (_ts(i["doc"].get("last_ok_ts")), _ts(i["doc"].get("vouched_ts")))
                  if t is not None]
        ok = max(stamps) if stamps else None
        want[i["id"]] = ("dormant" if ok is None or ok < fresh else "on", {})
    # the team moved on: a newer command of the same kind passing, this one not since
    runs = [i for i in live if i["kind"] == "runs" and want[i["id"]][0] == "on"]
    for old in runs:
        if old["doc"].get("keep"):
            continue   # a person turned it back on after it was replaced
        newer = [n for n in runs if n is not old
                 and n["doc"].get("check_kind") == old["doc"].get("check_kind")
                 and (_ts(n["doc"].get("first_ok_ts")) or now)
                 > (_ts(old["doc"].get("last_ok_ts")) or now)]
        if newer:
            by = max(newer, key=lambda n: _ts(n["doc"].get("last_ok_ts")) or now)
            want[old["id"]] = ("replaced", {"replaced_by": by["doc"]["text"],
                                            "replaced_by_id": by["id"]})
    # a fix follows the command it says works
    by_command = {(i["doc"].get("check_kind"), i["doc"].get("command")): i for i in items
                  if i["kind"] == "runs"}
    for f in (i for i in live if i["kind"] == "fix"):
        works = by_command.get((f["doc"].get("check_kind"), f["doc"].get("command")))
        if works is None:
            continue
        w_status = want.get(works["id"], (works["status"], {}))[0]
        if w_status in ("replaced", "dormant", "stale"):
            want[f["id"]] = (w_status, want.get(works["id"], ("", {}))[1])
    changes = {}
    for i in [*live, *(i for i in items if i["id"] in out_now)]:
        status, extra = want[i["id"]]
        doc = {k: v for k, v in i["doc"].items() if k not in ("replaced_by", "replaced_by_id")}
        doc.update(extra)
        if status != i["status"] or doc != i["doc"]:
            changes[i["id"]] = (status, doc)
    return changes, drop


async def fresh_days(store: Any) -> int:  # noqa: ANN401
    value = await store.get_setting(FRESH_SETTING)
    return int(value) if isinstance(value, int) and 1 <= value <= 365 else FRESH_DAYS


async def rotate(store: Any, repo: str, now: datetime | None = None) -> list[dict]:  # noqa: ANN401
    """Apply rotation to one repository; its items after."""
    now = now or datetime.now(UTC)
    items = await store.memory_items_of(repo)
    changes, drop = plan_rotation(items, now, await fresh_days(store))
    for item_id in drop:
        await store.delete_memory_item(item_id)
    for item_id, (status, doc) in changes.items():
        await store.update_memory_item(item_id, status, doc, now.isoformat())
    if changes or drop:
        items = await store.memory_items_of(repo)
    return items


# ── versions and drift ──────────────────────────────────────────────────────

def given_on_plan(state: Any, repo: str) -> bool:  # noqa: ANN401 - app.state
    """Whether this workspace's plan gives agents the team's memory."""
    from axor_backend.demo import CODING_REPO
    from axor_backend.licensing import active_license
    from axor_backend.tenancy import current_org_id

    if repo == CODING_REPO:
        return True
    lic = active_license(state, current_org_id())
    return lic is not None and lic.tier_at_least(PLAN_TIER)


def version_of(ids: list[int]) -> str:
    """What an agent was given, as one short id: "" for nothing."""
    if not ids:
        return ""
    return hashlib.sha256(",".join(str(i) for i in ids).encode()).hexdigest()[:10]


_WHY_GONE = {"replaced": "replaced", "dormant": "not confirmed lately",
             "stale": "stopped working", "off": "turned off", "on": "over the size limit"}


async def refresh(store: Any, repo: str,  # noqa: ANN401
                  label: str = "", *, given: bool = True) -> tuple[list[dict], str, list[int], str]:
    """Rotate, render, and log a change of what agents get. Returns (items,
    text, ids, version). ``given`` False (the plan gives agents nothing):
    agents get "" — and that is what is logged."""
    items = await rotate(store, repo)
    if not label:
        label = (await store.repo_labels()).get(repo, "")
    text, ids = for_agent(items, label) if given else ("", [])
    version = version_of(ids)
    last = await store.last_memory_change(repo)
    if (last["version"] if last else "") != version and (last or ids):
        before = set(last["ids"]) if last else set()
        by_id = {i["id"]: i for i in items}
        added = [{"id": i, "text": by_id[i]["doc"]["text"], "kind": by_id[i]["kind"],
                  "why": _why_on(by_id[i])} for i in ids if i not in before and i in by_id]
        removed = []
        for i in sorted(before - set(ids)):
            item = by_id.get(i)
            why = _WHY_GONE.get(item["status"], item["status"]) if item else "dropped"
            removed.append({"id": i, "text": item["doc"]["text"] if item else "", "why": why,
                            **({"replaced_by": item["doc"].get("replaced_by")}
                               if item and item["doc"].get("replaced_by") else {})})
        await store.add_memory_change(repo, version, ids, added, removed,
                                      last["version"] if last else "",
                                      datetime.now(UTC).isoformat())
    return items, text, ids, version


def _why_on(item: dict) -> str:
    d = item["doc"]
    if item["kind"] == "note":
        return f"written by {d.get('by') or 'someone on the team'}"
    if d.get("decided_by"):
        return f"turned on by {d['decided_by']}"
    if d.get("auto"):
        return "confirmed by two developers' agents"
    return "turned on"


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Fisher's exact test on [[a, b], [c, d]] — bad/good before, bad/good after."""
    from math import comb

    n1, n2, k = a + b, c + d, a + c
    total = comb(n1 + n2, k)
    if total == 0:
        return 1.0

    def p(x: int) -> float:
        return comb(n1, x) * comb(n2, k - x) / total

    observed = p(a)
    lo, hi = max(0, k - n2), min(k, n1)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= observed * (1 + 1e-9)))


async def session_outcome(store: Any, node_id: str) -> dict[str, bool]:  # noqa: ANN401
    """What the plane recorded of one session: did its first check fail, did
    the agent say checks passed when they failed (an audited case)."""
    first_failed = None
    for _i, _run, line in await store.node_events_after(node_id, 0, 5000):
        payload = line.get("payload") or {}
        check = payload.get("check") if line.get("kind") == "tool_result" else None
        if isinstance(check, dict):
            first_failed = not check.get("passed")
            break
    made_up = any((r.get("evidence") or []) for r in await store.runs_of_node(node_id))
    return {"first_check_failed": bool(first_failed), "made_up": made_up,
            "bad": bool(first_failed) or made_up}


async def compare(store: Any, repo: str, change: dict) -> dict:  # noqa: ANN401
    """The sessions given the version before a change against those given it
    after."""
    before = await store.sessions_given_version(repo, change["previous"])
    after = await store.sessions_given_version(repo, change["version"])
    sides = {}
    for name, nodes in (("before", before), ("after", after)):
        outs = [await session_outcome(store, n) for n in nodes[-50:]]
        sides[name] = {"sessions": len(outs),
                       "bad": sum(o["bad"] for o in outs),
                       "first_check_failed": sum(o["first_check_failed"] for o in outs),
                       "made_up": sum(o["made_up"] for o in outs)}
    b, a = sides["before"], sides["after"]
    if min(b["sessions"], a["sessions"]) < MIN_SESSIONS:
        verdict, p = "too_few", None
    else:
        p = fisher_two_sided(b["bad"], b["sessions"] - b["bad"], a["bad"], a["sessions"] - a["bad"])
        rb, ra = b["bad"] / b["sessions"], a["bad"] / a["sessions"]
        verdict = ("worse" if ra > rb else "better") if p < WORSE_P else "same"
    return {**sides, "p": p, "verdict": verdict}
