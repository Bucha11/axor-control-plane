"""The workspace's audit trail, to read and to take away: every control acted
on (axor_backend.audit, kind ``control``) and every credential act
(routers/keys, kind ``api_key``), newest first, as one list — and the whole of
it as CSV or JSON lines for a compliance archive. Admin only: it names who did
what to whose agent."""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from axor_backend import audit, permissions
from axor_backend.deps import PrincipalDep, StoreDep

router = APIRouter(prefix="/v1/audit", tags=["audit"])

KINDS = (audit.KIND, audit.KEY_KIND)
PAGE = 500
EXPORT_MAX = 100_000


def _row(kind: str, e: dict) -> dict[str, Any]:
    """One act, the same shape whichever log it came from."""
    if kind == audit.KIND:
        by = e.get("by") or {}
        return {"id": f"{kind}:{e.get('audit_id')}", "ts": e.get("ts", ""), "kind": kind,
                "action": e.get("action", ""), "who": by.get("who", ""),
                "role": by.get("role", ""), "target": e.get("target", ""),
                "detail": e.get("detail") or {}}
    by = e.get("by") or {}
    detail = {k: v for k, v in e.items()
              if k in ("scopes", "node_id", "label", "to", "from") and v not in (None, "", [])}
    return {"id": f"{kind}:{e.get('audit_id')}", "ts": e.get("ts", ""), "kind": kind,
            "action": f"key_{e.get('action', '')}",
            # a person's own key is labelled with their email (keys made before
            # the trail named people say so only there)
            "who": by.get("email") or _label_email(e) or by.get("id") or by.get("kind") or "",
            "role": "", "target": e.get("key_id", ""), "detail": detail}


def _label_email(e: dict) -> str:
    head = str(e.get("label") or "").split(" · ")[0].strip()
    return head if "@" in head and " " not in head and e.get("action") == "mint" else ""


async def _all(store: Any, kinds: tuple[str, ...], since: str, until: str,  # noqa: ANN401
               cap: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for kind in kinds:
        before = None
        while len(out) < cap:
            page = await store.vault_audit_entries(kind, limit=PAGE, before_id=before)
            if not page:
                break
            for e in page:
                ts = str(e.get("ts", ""))
                if until and ts > until:
                    continue
                if since and ts < since:
                    break
                out.append(_row(kind, e))
            if since and str(page[-1].get("ts", "")) < since:
                break
            before = page[-1]["audit_id"]
    out.sort(key=lambda r: r["ts"], reverse=True)
    return out[:cap]


def _matches(r: dict, action: str, who: str, target: str) -> bool:
    return ((not action or r["action"] == action)
            and (not who or who.lower() in r["who"].lower())
            and (not target or target.lower() in r["target"].lower()))


def _kinds(raw: str) -> tuple[str, ...]:
    wanted = tuple(k for k in raw.split(",") if k) if raw else KINDS
    if any(k not in KINDS for k in wanted):
        raise HTTPException(400, f"kinds are of {list(KINDS)}")
    return wanted


@router.get("")
async def read_audit(store: StoreDep, principal: PrincipalDep, limit: int = 100,
                     action: str = "", who: str = "", target: str = "",
                     since: str = "", until: str = "", kinds: str = "") -> dict:
    """The trail, newest first: ``action`` exact, ``who`` and ``target`` as a
    part of the name, ``since`` / ``until`` as ISO dates or instants."""
    permissions.require_admin(principal)
    limit = max(1, min(int(limit), 1000))
    rows = [r for r in await _all(store, _kinds(kinds), since, until, EXPORT_MAX)
            if _matches(r, action, who, target)]
    return {"items": rows[:limit], "total": len(rows),
            "actions": sorted({r["action"] for r in rows})}


@router.get("/export")
async def export_audit(store: StoreDep, principal: PrincipalDep,
                       format: str = "csv", since: str = "",  # noqa: A002
                       until: str = "", kinds: str = "") -> Response:
    """The whole trail in a range, as a file: CSV for a spreadsheet, JSON lines
    for an archive or a SIEM."""
    permissions.require_admin(principal)
    if format not in ("csv", "jsonl"):
        raise HTTPException(400, "format is csv or jsonl")
    rows = await _all(store, _kinds(kinds), since, until, EXPORT_MAX)
    name = f"axor-audit{'-' + since[:10] if since else ''}{'-' + until[:10] if until else ''}"
    if format == "jsonl":
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
        return Response(body, media_type="application/x-ndjson",
                        headers={"Content-Disposition": f'attachment; filename="{name}.jsonl"'})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["ts", "who", "role", "action", "target", "detail", "kind", "id"])
    for r in rows:
        w.writerow([r["ts"], r["who"], r["role"], r["action"], r["target"],
                    json.dumps(r["detail"], ensure_ascii=False) if r["detail"] else "",
                    r["kind"], r["id"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
