"""The workspace's audit trail, to read and to take away: every control acted
on (axor_backend.audit, kind ``control``) and every credential act
(routers/keys, kind ``api_key``), newest first, as one list — and the whole of
it as CSV or JSON lines for a compliance archive. Admin only: it names who did
what to whose agent."""
from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from axor_backend import audit, permissions
from axor_backend.deps import PrincipalDep, StoreDep

router = APIRouter(prefix="/v1/audit", tags=["audit"])

KINDS = (audit.KIND, audit.KEY_KIND)
PAGE = 500
VIEW_DAYS = 30
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
                if (until and ts > until) or (since and ts < since):
                    continue
                out.append(_row(kind, e))
            # pages come newest id first: a page wholly before the range ends it
            if since and all(str(e.get("ts", "")) < since for e in page):
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
async def read_audit(request: Request, store: StoreDep, principal: PrincipalDep, limit: int = 100,
                     action: str = "", who: str = "", target: str = "",
                     since: str = "", until: str = "", kinds: str = "") -> dict:
    """The trail, newest first: ``action`` exact, ``who`` and ``target`` as a
    part of the name, ``since`` / ``until`` as ISO dates or instants."""
    permissions.require_admin(principal)
    limit = max(1, min(int(limit), 1000))
    # below Business the trail is read for the last 30 days; all of it, and
    # its export, is the Business plan's
    window = None if permissions.on_business(request) else VIEW_DAYS
    if window:
        floor = (datetime.now(UTC) - timedelta(days=window)).isoformat()
        since = max(since, floor) if since else floor
    rows = [r for r in await _all(store, _kinds(kinds), since, until, EXPORT_MAX)
            if _matches(r, action, who, target)]
    return {"items": rows[:limit], "total": len(rows),
            "actions": sorted({r["action"] for r in rows}), "window_days": window}


@router.get("/retention")
async def get_retention(store: StoreDep, principal: PrincipalDep) -> dict:
    permissions.require_admin(principal)
    days = await store.get_setting(audit.RETENTION_SETTING)
    return {"days": days if isinstance(days, int) else None}


@router.put("/retention")
async def set_retention(body: dict, request: Request, store: StoreDep,
                        principal: PrincipalDep) -> dict:
    """How long this workspace keeps its audit trail: ``{"days": n}`` (30 or
    more), or ``{"days": null}`` to keep it for the deployment's window. Rows
    older go at the next sweep — and now."""
    from axor_backend.lifecycle import prune_audit_trail

    permissions.require_admin(principal)
    permissions.require_business(request, "the audit trail's retention")
    days = body.get("days")
    if days is not None and (not isinstance(days, int) or isinstance(days, bool)
                             or not audit.MIN_RETENTION_DAYS <= days <= 3650):
        raise HTTPException(400, f"days is {audit.MIN_RETENTION_DAYS} to 3650, or null")
    await store.set_setting(audit.RETENTION_SETTING, days)
    await audit.record(store, request, "audit_retention", str(days) if days else "the deployment's")
    return {"days": days, "pruned": await prune_audit_trail(store)}


@router.get("/report")
async def compliance_report(request: Request, store: StoreDep, principal: PrincipalDep,
                            month: str = "") -> dict:
    """One month of the trail as a reviewer reads it: how many acts of each
    kind and by whom, who acted on someone else's agent, every exception and
    permission granted or withdrawn, every brake with its reason."""
    import re

    permissions.require_admin(principal)
    permissions.require_business(request, "the monthly compliance report")
    month = month or datetime.now(UTC).strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise HTTPException(400, "month is YYYY-MM")
    y, m = int(month[:4]), int(month[5:])
    end = f"{y + (m == 12):04d}-{(m % 12) + 1:02d}-01"
    rows = [r for r in await _all(store, KINDS, f"{month}-01", "", EXPORT_MAX) if r["ts"] < end]
    profiles = await store.node_kinds()
    by_action: dict[str, int] = {}
    by_person: dict[str, dict[str, int]] = {}
    for r in rows:
        by_action[r["action"]] = by_action.get(r["action"], 0) + 1
        person = by_person.setdefault(r["who"] or "—", {})
        person[r["action"]] = person.get(r["action"], 0) + 1
    on_others = []
    for r in rows:
        dev = str((profiles.get(r["target"]) or {}).get("developer") or "")
        if r["kind"] == audit.KIND and "@" in dev and "@" in r["who"] \
                and dev.lower() != r["who"].lower():
            on_others.append({"ts": r["ts"], "who": r["who"], "action": r["action"],
                              "session": r["target"], "developer": dev})
    pick = ("exception_granted", "exception_withdrawn", "source_trusted", "source_untrusted",
            "rule_allowed", "rule_refused", "rules_mode")
    return {
        "month": month, "acts": len(rows), "by_action": dict(sorted(by_action.items())),
        "by_person": dict(sorted(by_person.items())),
        "on_others_sessions": on_others,
        "policy_changes": [r for r in rows if r["action"] in pick],
        "permission_changes": [r for r in rows if r["action"].startswith("permission_")],
        "brakes": [r for r in rows if r["action"] in ("brake_on", "brake_off")],
        # who joined, left or changed role; sign-in and session settings
        "access_changes": [r for r in rows if r["action"] in audit.IDENTITY_ACTS],
        "keys": [r for r in rows if r["kind"] == audit.KEY_KIND],
    }


@router.post("/identity", status_code=204)
async def from_identity(body: dict, request: Request, store: StoreDep,
                        principal: PrincipalDep) -> None:
    """An act on the workspace's people, sign-in or sessions — told by the
    login service, which keeps the people while this trail keeps the record.
    Only that service writes here: its own token (signed with the key this
    plane already trusts, for this workspace, two minutes) or the operator's."""
    from axor_backend.auth import MASTER

    if principal is not None and principal.kind != MASTER and not (
            principal.kind == "user" and principal.user_id == audit.IDENTITY_SERVICE):
        raise HTTPException(403, "only the login service records acts on people here")
    action = str(body.get("action") or "")
    if action not in audit.IDENTITY_ACTS:
        raise HTTPException(400, f"not an act on people: {action!r}")
    raw = body.get("detail") if isinstance(body.get("detail"), dict) else {}
    detail = {k: (v[:20] if isinstance(v, list) else v) for k, v in raw.items()
              if k in audit.IDENTITY_DETAIL and isinstance(v, str | int | bool | list)}
    await audit.record(store, request, action, str(body.get("target") or "")[:300],
                       who=str(body.get("who") or "the login service")[:300], **detail)


@router.get("/export")
async def export_audit(request: Request, store: StoreDep, principal: PrincipalDep,
                       format: str = "csv", since: str = "",  # noqa: A002
                       until: str = "", kinds: str = "") -> Response:
    """The whole trail in a range, as a file: CSV for a spreadsheet, JSON lines
    for an archive or a SIEM."""
    permissions.require_admin(principal)
    permissions.require_business(request, "exporting the audit trail")
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
