"""Find the step: every recorded tool call and answer across runs, filtered.

"On Thursday an agent dropped a production table" — the operator knows when,
roughly what, maybe which agent; not the run id. This answers with the STEPS
that match, newest first, each pointing at its run and seq so Replay opens on
it. The filters are what an operator remembers, not kernel vocabulary:

- when: an ISO time window (each event's own ``ts``);
- which agent: part of a node id;
- which tool: part of a tool name;
- what happened: refused · would have been refused (observe mode) · let through
  · an answer that was outside text;
- what it did: ran code · changed files · sent something out · deleted or wiped;
- text: anything in the arguments, the answer's preview or the refusal reason
  ("prod", "DROP", "rm -rf").

What a call did is read from the call itself — the tool and its arguments —
because the coding sidecars record no normalized projection: a ``Bash`` call is
"ran code", and a ``DROP TABLE`` inside it is also "deleted or wiped".
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

OUTCOMES = ("refused", "would_refuse", "let_through", "outside")
DOES = ("runs_code", "changes_files", "sends_out", "deletes")

_RUNS_CODE = {"bash", "shell", "exec", "run_shell", "execute", "terminal", "run_command",
              "local_shell", "python", "code_interpreter", "exec_command"}
_CHANGES_FILES = {"write", "edit", "multiedit", "apply_patch", "notebookedit", "write_file",
                  "create_file", "edit_file", "delete_file", "str_replace_editor"}
_SENDS_OUT_NAME = re.compile(r"post|send|email|mail|slack|webhook|upload|publish|tweet|"
                             r"message|notify|http_request", re.I)
_SENDS_OUT_CMD = re.compile(r"\b(curl|wget|scp|rsync|nc|ssh|sftp)\b|git\s+push", re.I)
_DELETES = re.compile(
    r"\brm\s+-\w*[rf]|\bdrop\s+(table|database|schema|index|collection)\b|\btruncate\b"
    r"|\bdelete\s+from\b|kubectl\s+delete|terraform\s+destroy|git\s+push\s+.*(--force|-f\b)"
    r"|git\s+reset\s+--hard|docker\s+(rm|system\s+prune)|\bmkfs\b|\bdd\s+if=|deleteobject"
    r"|flushall|dropdatabase", re.I)
_DELETES_NAME = re.compile(r"delete|drop|destroy|remove|purge|wipe", re.I)


def _ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        t = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def what_it_did(tool: str, args: dict[str, Any], normalized: object = None) -> list[str]:
    name = tool.lower()
    text = json.dumps(args, ensure_ascii=False)
    out: list[str] = []
    if name in _RUNS_CODE:
        out.append("runs_code")
    op = normalized.get("operation") if isinstance(normalized, dict) else None
    if name in _CHANGES_FILES or op == "file_write":
        out.append("changes_files")
    dest = normalized.get("destination_kind") if isinstance(normalized, dict) else None
    if (_SENDS_OUT_NAME.search(name) or (name in _RUNS_CODE and _SENDS_OUT_CMD.search(text))
            or dest not in (None, "none", "workdir", "local")):
        out.append("sends_out")
    if _DELETES.search(text) or _DELETES_NAME.search(name):
        out.append("deletes")
    return out


def _summary(args: dict[str, Any]) -> str:
    for key in ("command", "cmd", "query", "sql", "url", "file_path", "path", "to", "channel"):
        v = args.get(key)
        if isinstance(v, str) and v:
            return v[:200]
    return json.dumps(args, ensure_ascii=False)[:200] if args else ""


def search(
    rows: list[tuple[str, dict[str, Any]]], *,
    since: str | None = None, until: str | None = None, agent: str = "",
    tool: str = "", outcome: str = "", does: str = "", text: str = "", limit: int = 200,
) -> list[dict[str, Any]]:
    """``rows`` are (run_id, event line), newest first."""
    lo, hi = _ts(since), _ts(until)
    # answers that were outside text, per run — a later call that quotes one
    # is marked as using it
    outside_refs: dict[str, set[str]] = {}
    for run_id, line in rows:
        p = line.get("payload") or {}
        if line.get("kind") == "tool_result" and (p.get("root") or {}).get("sources"):
            if isinstance(p.get("value_ref"), str):
                outside_refs.setdefault(run_id, set()).add(p["value_ref"])
    needle = text.strip().lower()
    found: list[dict[str, Any]] = []
    for run_id, line in rows:
        kind = line.get("kind")
        if kind not in ("tool_call", "tool_result"):
            continue
        p = line.get("payload") or {}
        node = str(line.get("node_id", ""))
        name = str(p.get("tool", ""))
        when = _ts(line.get("ts"))
        if (lo or hi) and when is None:
            continue
        if lo and when and when < lo:
            continue
        if hi and when and when > hi:
            continue
        if agent and agent.lower() not in node.lower():
            continue
        if tool and tool.lower() not in name.lower():
            continue
        args = p.get("args") if isinstance(p.get("args"), dict) else {}
        if kind == "tool_call":
            refused = line.get("verdict") == "deny"
            label = ("refused" if refused else "would_refuse" if p.get("would_deny")
                     else "let_through")
            did = what_it_did(name, args, p.get("normalized"))
            refs = p.get("arg_refs") if isinstance(p.get("arg_refs"), dict) else {}
            # the sidecar ships what a call's arguments carry (`carries`)
            from_outside = (bool((p.get("driving_root") or {}).get("sources"))
                            or bool(p.get("carries"))
                            or any(r in outside_refs.get(run_id, set()) for r in refs.values()))
            reason = p.get("reason") or (p.get("would_deny") or {}).get("reason") or ""
        else:
            label = "outside" if (p.get("root") or {}).get("sources") else "answered"
            did, from_outside, reason = [], label == "outside", ""
        if outcome and outcome != label:
            continue
        if does and does not in did:
            continue
        preview = str(p.get("preview", ""))
        if needle and needle not in " ".join(
                (json.dumps(args, ensure_ascii=False), preview, str(reason))).lower():
            continue
        found.append({
            "run_id": run_id, "node_id": node, "seq": line.get("seq"),
            "ts": line.get("ts"), "kind": kind, "tool": name, "outcome": label,
            "does": did, "from_outside": from_outside,
            "what": _summary(args) if kind == "tool_call" else preview[:200],
            "reason": str(reason)[:300],
        })
        if len(found) >= limit:
            break
    return found
