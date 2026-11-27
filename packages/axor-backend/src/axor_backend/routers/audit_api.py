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

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from axor_backend import audit, compliance, permissions
from axor_backend.attestations import ATTESTATION_FACT_TYPE
from axor_backend.deps import PrincipalDep, StateDep, StoreDep
from axor_backend.tenancy import current_org_id

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
    every = await _all(store, _kinds(kinds), since, until, EXPORT_MAX)
    rows = [r for r in every if _matches(r, action, who, target)]
    # the filter's choices: every act on the trail, not only those the current
    # filter kept — picking one used to leave only that one to pick (QA-020)
    return {"items": rows[:limit], "total": len(rows),
            "actions": sorted({r["action"] for r in every}), "window_days": window}


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


# ── Enterprise: the compliance pack, and the hold that keeps it alive ───────

@router.get("/holds")
async def list_holds(request: Request, store: StoreDep, principal: PrincipalDep) -> dict:
    """Legal holds: while one is open nothing on the trail is pruned, whatever
    the workspace's retention says."""
    permissions.require_admin(principal)
    permissions.require_enterprise(request, "a legal hold on the audit trail")
    return {"holds": await compliance.holds(store)}


@router.post("/holds", status_code=201)
async def place_hold(body: dict, request: Request, store: StoreDep,
                     principal: PrincipalDep) -> dict:
    """``{"reason": "…"}`` — a reason is required: a hold nobody can explain
    later is a trail nobody can prune."""
    permissions.require_admin(principal)
    permissions.require_enterprise(request, "a legal hold on the audit trail")
    reason = str(body.get("reason") or "").strip()
    if len(reason) < 3:
        raise HTTPException(400, "a hold needs a reason — what it is for, in a few words")
    return await compliance.place(store, request, reason, audit.actor(request)["who"])


@router.delete("/holds/{hold_id}")
async def release_hold(hold_id: str, request: Request, store: StoreDep,
                       principal: PrincipalDep) -> dict:
    permissions.require_admin(principal)
    permissions.require_enterprise(request, "a legal hold on the audit trail")
    released = await compliance.release(store, request, hold_id, audit.actor(request)["who"])
    if released is None:
        raise HTTPException(404, "no open hold by that id")
    return released


async def _chains(store: Any) -> dict[str, Any]:  # noqa: ANN401
    from axor_backend import audit_chain

    return {kind: audit_chain.verify(await store.vault_audit_chain(kind), current_org_id(), kind)
            for kind in KINDS}


@router.get("/verify")
async def verify_trail(store: StoreDep, principal: PrincipalDep) -> dict:
    """Walk the audit trail's hash chains: whole, or the first row where one
    breaks and why — an edit, a removal from the middle, a row slipped in."""
    permissions.require_admin(principal)
    chains = await _chains(store)
    return {"ok": all(c["ok"] for c in chains.values()), "chains": chains}


@router.get("/compliance")
async def compliance_pack(request: Request, state: StateDep, store: StoreDep,
                          principal: PrincipalDep, format: str = "json",  # noqa: A002
                          from_: str = Query("", alias="from"), to: str = "",
                          sign: str = "") -> Response:
    """A period of the whole governed surface as one document — who oversaw
    which agent and why, every attestation, policy and access change, and what
    each answers — with a digest over the body.

    ``sign=<key id>`` signs the digest through the deployment's signing custody
    (the caller must be an operator for that key), so the copy an auditor holds
    can be checked against a key they were given.
    """
    permissions.require_admin(principal)
    permissions.require_enterprise(request, "the compliance pack")
    frm, until = _period(from_, to)
    rows = [r for r in await _all(store, KINDS, frm, until, EXPORT_MAX) if r["ts"] < until]
    attestations = await store.facts_between(frm, until, ATTESTATION_FACT_TYPE)
    usage = await store.developer_usage(frm[:10], until[:10])
    lic = _license_of(state)
    body = compliance.build(
        organization=current_org_id(), frm=frm[:10], to=until[:10],
        generated_by=audit.actor(request)["who"],
        tier=lic[0], licensed_to=lic[1],
        rows=rows, attestations=attestations,
        fleet=await store.node_kinds(),
        developers=[d["developer"] for d in usage.get("developers", [])],
        held=await compliance.holds(store),
        truncated_at=EXPORT_MAX if len(rows) >= EXPORT_MAX else 0,
        retention_days=keep if isinstance(keep := await store.get_setting(
            audit.RETENTION_SETTING), int) else None,
        chain=await _chains(store),
    )
    if sign:
        # Signing is the vault's custody, and it keeps its own wall: a
        # compliance pack must not be a way around the signing token.
        from axor_backend.routers.vault import vault_gate
        from axor_backend.vault_signing import SigningCustody, SignRefused

        await vault_gate(request, "signing")
        try:
            signed = await SigningCustody(store).sign(
                operator=audit.actor(request)["who"], key_id=sign,
                payload=compliance.signable(body),
                principal=str(getattr(principal, "key_id", "") or ""))
        except SignRefused as exc:
            raise HTTPException(403, exc.reason) from exc
        body["integrity"] |= {"signature": signed["signature_hex"],
                              "signed_by": signed["key_id"]}
    name = f"axor-compliance-{frm[:10]}_{until[:10]}"
    if format == "html":
        return Response(compliance.as_html(body), media_type="text/html; charset=utf-8",
                        headers={"Content-Disposition": f'inline; filename="{name}.html"'})
    return Response(json.dumps(body, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}.json"'})


def _period(frm: str, to: str) -> tuple[str, str]:
    """The range asked for, as timestamps. Defaults to the quarter just ended,
    because that is the period a reviewer asks about."""
    import re

    today = datetime.now(UTC).date()
    if not frm:
        q = (today.month - 1) // 3
        year, start_q = (today.year - 1, 4) if q == 0 else (today.year, q)
        frm = f"{year}-{(start_q - 1) * 3 + 1:02d}-01"
        to = to or f"{year + (start_q == 4):04d}-{(start_q * 3) % 12 + 1:02d}-01"
    for value in (frm, to):
        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise HTTPException(400, "from and to are YYYY-MM-DD")
    if not to:
        to = today.isoformat()
    if to <= frm:
        raise HTTPException(400, "the period ends after it starts")
    return frm, to


def _license_of(state: Any) -> tuple[str, str]:  # noqa: ANN401 - app.state is dynamic
    from axor_backend.licensing import active_license, stored_license

    lic = active_license(state, current_org_id()) or stored_license(state, current_org_id())
    return (lic.workspace_tier, lic.organization) if lic else ("community", "")


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
