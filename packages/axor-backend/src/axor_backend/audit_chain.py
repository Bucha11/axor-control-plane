"""The custody logs as hash chains: what makes the audit trail tamper-evident.

Each row of a log (one org, one kind) carries the hash of the row before it
and its own: ``sha256(prev | org | kind | ts | JCS(entry))``. Retention prunes
from the oldest end, so a chain may START later than it once did — the oldest
row kept is the anchor and the walk says so. What it catches is the rest: a
row edited (its hash no longer matches), removed from the middle, or slipped in
(the next row's ``prev_hash`` no longer names it).

It proves the trail was not changed by anyone without the code that writes it;
someone who can rewrite the database AND recompute every hash after the edit
can still rewrite history. Pinning the head — the compliance pack carries it,
signed — is what closes that: a later chain that no longer contains a signed
head was rewritten.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _canonical(entry: Any) -> bytes:  # noqa: ANN401
    """One encoding: sorted keys, compact, UTF-8. Only this module hashes it."""
    return json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode()


def row_hash(prev: str, org: str, kind: str, ts: str, entry: Any) -> str:  # noqa: ANN401
    h = hashlib.sha256()
    for part in (prev, org, kind, ts):
        h.update(part.encode())
        h.update(b"|")
    h.update(_canonical(entry))
    return h.hexdigest()


def verify(rows: list[dict[str, Any]], org: str, kind: str) -> dict[str, Any]:
    """Walk one log in write order. ``ok`` and the head when whole; else the
    first row where it breaks, and why."""
    chained = [r for r in rows if r.get("row_hash")]
    legacy = len(rows) - len(chained)
    if not chained:
        return {"ok": True, "rows": 0, "unchained": legacy, "head": None, "anchor": None}
    prev = chained[0]["prev_hash"] or ""
    for r in chained:
        if (r["prev_hash"] or "") != prev:
            return {"ok": False, "broken_at": r["id"], "rows": len(chained),
                    "reason": "a row before this one was removed, or one was slipped in"}
        if row_hash(prev, org, kind, r["ts"], r["entry"]) != r["row_hash"]:
            return {"ok": False, "broken_at": r["id"], "rows": len(chained),
                    "reason": "this row was changed after it was written"}
        prev = r["row_hash"]
    return {"ok": True, "rows": len(chained), "unchained": legacy,
            "head": {"id": chained[-1]["id"], "hash": prev},
            # the oldest row kept: what came before it was pruned by retention
            # (or predates the chain) — the walk starts here
            "anchor": {"id": chained[0]["id"], "prev_hash": chained[0]["prev_hash"] or ""}}
