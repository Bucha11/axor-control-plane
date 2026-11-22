"""Pair sessions: someone's coding-agent session, watched live by others who
can send the agent a note.

A developer opens a pair on their own session (``POST /v1/pair``) and gets a
link. Whoever opens it signed in sees the session's steps as they happen and
can write a note; the developer's axor sidecar fetches the notes and gives
them to the agent at its next step (``GET /v1/coding/notes/{node}``), then
says when (``POST …/delivered``), so the pair sees the note land.

Who may open the link:
- anyone in the same workspace;
- someone from outside it — their own account, any plan, a free one too —
  only if the pair was opened for people outside the team AND the inviting
  workspace is on a paid plan. That is checked on every read, not once: a
  plan that lapses closes the door to outside guests mid-session. At most
  ``OUTSIDE_CAP`` outside guests per pair. A guest session (no account) is
  never let in (guests.ACCOUNT_ONLY).

The token resolves across tenants (Store.pair_by_token); once the caller is
let in, the request adopts the pair's tenant — the share-link pattern.
"""
from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from axor_backend.deps import PrincipalDep, StoreDep
from axor_backend.tenancy import PUBLIC_ORG, current_org_id, set_current_org

router = APIRouter(tags=["pair"])

PAIR_TTL = timedelta(hours=8)
OUTSIDE_CAP = 3
NOTE_MAX = 2000
FEED_LIMIT = 200
NOTE_KEEP = timedelta(hours=1)   # a note not taken within this is not given late


def _now() -> datetime:
    return datetime.now(UTC)


def _who(principal: Any) -> str:  # noqa: ANN401 - Principal | None
    if principal is None:
        return ""
    return str(getattr(principal, "email", "") or getattr(principal, "key_id", "") or "")


def _org_of(principal: Any) -> str:  # noqa: ANN401
    return (getattr(principal, "org", None) or PUBLIC_ORG) if principal is not None else PUBLIC_ORG


def _paid(request: Request, org: str) -> bool:
    from axor_backend.ee.entitlements import workspace_tier
    from axor_backend.licensing import active_license

    return workspace_tier(active_license(request.app.state, org)) != "community"


def _plan_required(detail: str) -> HTTPException:
    return HTTPException(402, {"code": "plan_required", "detail": detail})


# ── the developer's side ─────────────────────────────────────────────────────

@router.post("/v1/pair", status_code=201)
async def open_pair(body: dict, request: Request, store: StoreDep,
                    principal: PrincipalDep) -> dict:
    node_id = str(body.get("node_id", ""))
    if not node_id or node_id not in await store.node_kinds():
        raise HTTPException(404, "no coding session by that id in this workspace")
    outside = bool(body.get("outside"))
    if outside and not _paid(request, current_org_id()):
        raise _plan_required("inviting people from outside the team is on the paid plan — "
                             "teammates can join on any plan")
    token = secrets.token_urlsafe(16)
    now = _now()
    await store.create_pair(token, node_id, _who(principal), now.isoformat(),
                            (now + PAIR_TTL).isoformat(), outside)
    return {"token": token, "node_id": node_id, "outside": outside,
            "expires_ts": (now + PAIR_TTL).isoformat()}


@router.get("/v1/pair")
async def list_pairs(store: StoreDep) -> dict:
    return {"pairs": await store.list_pairs(_now().isoformat())}


@router.delete("/v1/pair/{token}")
async def close_pair(token: str, store: StoreDep) -> dict:
    pair = await store.pair_by_token(token)
    if pair is None or pair["org_id"] != current_org_id():
        raise HTTPException(404, "no such pair session in this workspace")
    await store.update_pair(token, closed_ts=_now().isoformat())
    return {"closed": True}


# ── whoever holds the link ───────────────────────────────────────────────────

async def _let_in(token: str, request: Request, store: StoreDep,
                  principal: Any) -> tuple[dict, str]:  # noqa: ANN401
    """The pair, and "teammate" or "guest" — or the reason not."""
    pair = await store.pair_by_token(token)
    now = _now().isoformat()
    if pair is None or pair["closed_ts"] or pair["expires_ts"] <= now:
        raise HTTPException(404, "this pair session has ended, or the link is wrong")
    if _org_of(principal) == pair["org_id"]:
        set_current_org(pair["org_id"])
        return pair, "teammate"
    if getattr(principal, "kind", None) != "user":
        raise HTTPException(403, {"code": "account_required",
                                  "detail": "sign in to join a pair session"})
    if not pair["outside"]:
        raise HTTPException(403, {"code": "team_only",
                                  "detail": "this pair session is for the team only — "
                                            "ask whoever sent the link to open it to "
                                            "people outside the team"})
    if not _paid(request, pair["org_id"]):
        raise _plan_required("the inviting team's plan does not let people from outside "
                             "the team join — inviting outside guests is on the paid plan")
    who = _who(principal)
    guests = pair["guests"]
    if not any(g.get("who") == who for g in guests):
        if len(guests) >= OUTSIDE_CAP:
            raise HTTPException(403, {"code": "pair_full",
                                      "detail": f"this pair session already has "
                                                f"{OUTSIDE_CAP} guests from outside the team"})
        guests = [*guests, {"who": who, "org": _org_of(principal), "ts": now}]
        await store.update_pair(token, guests=guests)
        pair = {**pair, "guests": guests}
    set_current_org(pair["org_id"])
    return pair, "guest"


def _step(event_id: int, line: dict) -> dict | None:
    """One recorded line, as the pair view shows it."""
    kind = line.get("kind")
    p = line.get("payload") or {}
    base = {"id": event_id, "ts": line.get("ts", ""), "kind": kind}
    if kind == "tool_call":
        args = p.get("args") or {}
        target = (args.get("command") or args.get("file_path") or args.get("url")
                  or args.get("pattern") or args.get("path") or args.get("skill") or "")
        return {**base, "tool": p.get("tool", ""), "target": str(target)[:300],
                "refused": line.get("verdict") == "deny",
                "reason": str(p.get("reason") or "")[:300]}
    if kind == "tool_result":
        check = p.get("check") or None
        return {**base, "tool": p.get("tool", ""), "failed": p.get("status") == "error",
                "preview": str(p.get("preview") or "")[:300],
                "check": ({k: check.get(k) for k in ("kind", "passed", "summary")}
                          if check else None)}
    if kind == "claim":
        return {**base, "text": str(p.get("text") or "")[:2000]}
    if kind in ("task", "prompt", "intent"):
        return {**base, "kind": "task", "text": str(p.get("text") or p.get("prompt") or "")[:1000]}
    return None


@router.get("/v1/pair/{token}/feed")
async def pair_feed(token: str, request: Request, store: StoreDep, principal: PrincipalDep,
                    after: int = 0) -> dict:
    pair, you = await _let_in(token, request, store, principal)
    rows = await store.node_events_after(pair["node_id"], max(after, 0), FEED_LIMIT)
    steps = [s for i, _run, line in rows if (s := _step(i, line)) is not None]
    profile = (await store.node_kinds()).get(pair["node_id"], {})
    posture = ((await store.get_reported(pair["node_id"])) or {}).get("level") or ""
    desired = await store.get_desired(pair["node_id"])
    paused = bool(desired and desired[1].get("paused"))
    return {
        "pair": {"node_id": pair["node_id"], "agent": profile.get("agent", ""),
                 "title": profile.get("title", ""),
                 "created_by": pair["created_by"], "created_ts": pair["created_ts"],
                 "expires_ts": pair["expires_ts"], "outside": pair["outside"],
                 "guests": [g["who"] for g in pair["guests"]], "you": you,
                 "paused": paused, "stopped": bool(desired and desired[1].get("stopped")),
                 "desired_version": desired[0] if desired else 0, "level": posture},
        "steps": steps,
        "cursor": rows[-1][0] if rows else after,
        "notes": await store.pair_notes_of(token),
    }


@router.post("/v1/pair/{token}/notes", status_code=201)
async def send_note(token: str, body: dict, request: Request, store: StoreDep,
                    principal: PrincipalDep) -> dict:
    pair, you = await _let_in(token, request, store, principal)
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "a note has text")
    if len(text) > NOTE_MAX:
        raise HTTPException(413, f"a note is at most {NOTE_MAX} characters")
    by = _who(principal) or ("a teammate" if you == "teammate" else "a guest")
    note_id = await store.add_pair_note(token, pair["node_id"], by, text, _now().isoformat(),
                                        outside=you == "guest")
    return {"id": note_id}


# ── the developer's sidecar ──────────────────────────────────────────────────

def _speaks_for(principal: Any, node_id: str) -> None:  # noqa: ANN401
    if principal is not None and not principal.may_speak_for(node_id):
        raise HTTPException(403, "this key is bound to another node")


@router.get("/v1/coding/notes/{node_id}")
async def notes_for_agent(node_id: str, store: StoreDep, principal: PrincipalDep) -> dict:
    _speaks_for(principal, node_id)
    since = (_now() - NOTE_KEEP).isoformat()
    return {"notes": await store.pending_notes(node_id, since)}


@router.post("/v1/coding/notes/{node_id}/delivered")
async def notes_delivered(node_id: str, body: dict, store: StoreDep,
                          principal: PrincipalDep) -> dict:
    _speaks_for(principal, node_id)
    ids = [int(i) for i in body.get("ids") or [] if isinstance(i, int)]
    n = await store.mark_notes_delivered(node_id, ids, _now().isoformat(),
                                         str(body.get("at") or ""))
    return {"delivered": n}
