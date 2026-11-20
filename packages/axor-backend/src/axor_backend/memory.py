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
and anything long. A kept-out command is listed — "kept out: it came from a
web page" — and never given to an agent.

An item found automatically is suggested; one click turns it on, and it turns
on by itself once it passed for two different developers. One that then fails
twice in a row is marked stopped working and no longer given; passing again
puts it back.
"""
from __future__ import annotations

import re
from typing import Any

KINDS = ("runs", "fix", "note")
GIVEN = "on"
STATUSES = ("suggested", "on", "off", "stale", "kept_out")
AUTO_ON_DEVELOPERS = 2
STALE_AFTER = 2
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
               "fails": 0, "last_ok_ts": ts, "given": 0, "auto": False, "reason": reason or ""}
        await store.add_memory_item(repo, key, kind, "kept_out" if reason else "suggested",
                                    doc, ts)
        return
    doc, status = item["doc"], item["status"]
    if passed:
        if developer and developer not in doc["developers"]:
            doc["developers"].append(developer)
        doc["evidence"] = ([evidence] + doc["evidence"])[:EVIDENCE_KEEP]
        doc["fails"], doc["last_ok_ts"] = 0, ts
        if status == "suggested" and len(doc["developers"]) >= AUTO_ON_DEVELOPERS:
            status, doc["auto"] = "on", True
        elif status == "stale":
            status = "on"
    else:
        doc["fails"] = doc.get("fails", 0) + 1
        doc["last_fail_ts"] = ts
        if status == "on" and doc["fails"] >= STALE_AFTER:
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
