"""Pair sessions: someone's coding-agent session, watched live by others who
can send the agent a note.

Only by invitation. A developer opens a pair on their OWN session (``POST
/v1/pair``) — nobody else can, an owner or admin neither — naming teammates to
invite, and gets a link. A teammate who wants to watch asks (``POST
/v1/pair/requests``); the developer is told in their own agent and lets them
in, or not. Whoever is let in sees the session's steps as they happen and can
write a note; the developer's axor sidecar fetches the notes and gives them to
the agent at its next step (``GET /v1/coding/notes/{node}``), then says when
(``POST …/delivered``), so the pair sees the note land. The developer is told
in their agent who joined, and every note their agent was given.

The list of pairs (``GET /v1/pair``) is the caller's: those they opened and
those they were invited to — never another developer's link.

Who may open the link:
- a teammate the developer invited, or sent the link to;
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

from fastapi import APIRouter, Header, HTTPException, Request

from axor_backend.deps import PrincipalDep, StoreDep
from axor_backend.tenancy import PUBLIC_ORG, current_org_id, set_current_org

router = APIRouter(tags=["pair"])

PAIR_TTL = timedelta(hours=8)
OUTSIDE_CAP = 3
NOTE_MAX = 2000
FEED_LIMIT = 200
NOTE_KEEP = timedelta(hours=1)   # a note not taken within this is not given late


def _signed_note(body: dict, node_id: str, text: str) -> dict | None:
    """An operator's signature over this note, as the browser sent it — kept
    for the sidecar to verify with its own keys (the plane only checks that it
    signs THIS text to THIS session). None when unsigned."""
    st = body.get("signed")
    if st is None:
        return None
    payload = st.get("payload") if isinstance(st, dict) else None
    if not isinstance(payload, dict) or payload.get("kind") != "note" \
            or payload.get("node_id") != node_id or payload.get("text") != text \
            or not isinstance(st.get("operator"), str) or not isinstance(st.get("sig"), str):
        raise HTTPException(400, "signed is {payload: {kind: note, node_id, text, ts}, "
                                 "operator, sig} over this note")
    return {"payload": payload, "operator": st["operator"], "sig": st["sig"]}


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

def _person(principal: Any) -> bool:  # noqa: ANN401
    """A signed-in person — the only caller whose sessions are "theirs". An open
    plane (no identity) or an operator key has one operator: whoever runs it."""
    return getattr(principal, "kind", None) == "user" and bool(getattr(principal, "email", ""))


def _mine(principal: Any, profile: dict) -> bool:  # noqa: ANN401
    """The caller's own session. A session whose developer is an opaque id (a
    machine key, not a person's own) names nobody: it is the team's, as before."""
    if not _person(principal):
        return True
    dev = str(profile.get("developer") or "").lower()
    return "@" not in dev or dev == str(principal.email).lower()


def not_yours(profile: dict) -> HTTPException:
    dev = profile.get("developer") or "its developer"
    return HTTPException(403, {"code": "not_your_session",
                               "detail": f"this is {dev}'s session — only they open a pair "
                                         f"on it. Ask to pair: they are told in their agent "
                                         f"and let you in."})


async def _open(store: Any, node_id: str, by: str, outside: bool,  # noqa: ANN401
                invited: list[str]) -> dict:
    token = secrets.token_urlsafe(16)
    now = _now()
    await store.create_pair(token, node_id, by, now.isoformat(),
                            (now + PAIR_TTL).isoformat(), outside, invited)
    return {"token": token, "node_id": node_id, "outside": outside, "invited": invited,
            "expires_ts": (now + PAIR_TTL).isoformat()}


@router.post("/v1/pair", status_code=201)
async def open_pair(body: dict, request: Request, store: StoreDep,
                    principal: PrincipalDep) -> dict:
    node_id = str(body.get("node_id", ""))
    profile = (await store.node_kinds()).get(node_id)
    if not node_id or profile is None:
        raise HTTPException(404, "no coding session by that id in this workspace")
    if not _mine(principal, profile):
        raise not_yours(profile)
    outside = bool(body.get("outside"))
    if outside and not _paid(request, current_org_id()):
        raise _plan_required("inviting people from outside the team is on the paid plan — "
                             "teammates can join on any plan")
    invited = [str(e).strip().lower() for e in body.get("invite") or [] if str(e).strip()][:20]
    from axor_backend.funnel import used

    await used(store, "pair_open")
    opened = await _open(store, node_id, _who(principal), outside, invited)
    from axor_backend import audit

    await audit.record(store, request, "pair_open", node_id,
                       **({"invited": invited} if invited else {}),
                       **({"outside": True} if outside else {}))
    return opened


def _opened_by_developer(pair: dict, profile: dict) -> bool:
    """A pair opened by someone other than the session's developer (before
    pairs were by invitation) is void: it lets nobody in."""
    by, dev = str(pair["created_by"]).lower(), str(profile.get("developer") or "").lower()
    return not by or "@" not in by or "@" not in dev or by == dev


def _visible(pair: dict, principal: Any) -> bool:  # noqa: ANN401
    """Whose pair this is to see in a list: the one who opened it, a teammate
    invited by name, or one already let in by its link."""
    if not _person(principal):
        return True
    me = str(principal.email).lower()
    return (str(pair["created_by"]).lower() == me or me in pair["invited"]
            or any(str(g.get("who", "")).lower() == me for g in pair["guests"]))


@router.get("/v1/pair")
async def list_pairs(store: StoreDep, principal: PrincipalDep) -> dict:
    kinds = await store.node_kinds()
    pairs = [p for p in await store.list_pairs(_now().isoformat())
             if _visible(p, principal) and _opened_by_developer(p, kinds.get(p["node_id"], {}))]
    for p in pairs:
        prof = kinds.get(p["node_id"], {})
        p["title"], p["agent"] = prof.get("title", ""), prof.get("agent", "")
        p["developer"] = prof.get("developer", "")
        p["yours"] = not _person(principal) or \
            str(p["created_by"]).lower() == str(principal.email).lower()
    return {"pairs": pairs}


@router.delete("/v1/pair/{token}")
async def close_pair(token: str, request: Request, store: StoreDep,
                     principal: PrincipalDep) -> dict:
    pair = await store.pair_by_token(token)
    if pair is None or pair["org_id"] != current_org_id():
        raise HTTPException(404, "no such pair session in this workspace")
    if _person(principal) and str(pair["created_by"]).lower() != str(principal.email).lower():
        raise HTTPException(403, "only whoever opened the pair session ends it")
    await store.update_pair(token, closed_ts=_now().isoformat())
    from axor_backend import audit

    await audit.record(store, request, "pair_end", pair["node_id"])
    return {"closed": True}


# ── asking to pair ───────────────────────────────────────────────────────────

@router.post("/v1/pair/requests", status_code=201)
async def ask_to_pair(body: dict, store: StoreDep, principal: PrincipalDep) -> dict:
    """A teammate asks to watch someone's session. The developer is told in
    their agent, and sees it on Pair sessions, where they let them in."""
    node_id = str(body.get("node_id", ""))
    profile = (await store.node_kinds()).get(node_id)
    if not node_id or profile is None or profile.get("kind") != "coding":
        raise HTTPException(404, "no coding session by that id in this workspace")
    if not _person(principal):
        raise HTTPException(403, "sign in to ask to pair — the developer is told who asks")
    if _mine(principal, profile):
        raise HTTPException(400, "this is your own session — open a pair on it")
    rid = await store.add_pair_request(node_id, _who(principal), str(profile.get("developer")),
                                       _now().isoformat())
    from axor_backend.funnel import used

    await used(store, "ask_to_pair")
    return {"id": rid, "status": "pending"}


@router.get("/v1/pair/requests")
async def pair_requests(store: StoreDep, principal: PrincipalDep) -> dict:
    """Requests to me (on my sessions), and mine (with the link once I am let in)."""
    since = (_now() - PAIR_TTL).isoformat()
    if not _person(principal):
        return {"to_me": [r for r in await store.pair_requests_of(since=since)
                          if r["status"] == "pending"], "mine": []}
    me = str(principal.email)
    to_me = [r for r in await store.pair_requests_of(developer=me, since=since)
             if r["status"] == "pending"]
    for r in to_me:
        r.pop("token", None)
    return {"to_me": to_me, "mine": await store.pair_requests_of(by=me, since=since)}


@router.post("/v1/pair/requests/{request_id}")
async def decide_request(request_id: int, body: dict, request: Request, store: StoreDep,
                         principal: PrincipalDep) -> dict:
    found = [r for r in await store.pair_requests_of() if r["id"] == request_id]
    if not found:
        raise HTTPException(404, "no such request")
    req = found[0]
    profile = (await store.node_kinds()).get(req["node_id"], {})
    if not _mine(principal, profile):
        raise not_yours(profile)
    if req["status"] != "pending":
        return {"id": request_id, "status": req["status"]}
    from axor_backend import audit

    if not body.get("accept"):
        await store.decide_pair_request(request_id, "declined", _now().isoformat())
        await audit.record(store, request, "pair_declined", req["node_id"], asked_by=req["by"])
        return {"id": request_id, "status": "declined"}
    pair = await _open(store, req["node_id"], _who(principal), False, [req["by"]])
    from axor_backend.funnel import used

    await used(store, "pair_let_in")
    await store.decide_pair_request(request_id, "accepted", _now().isoformat(), pair["token"])
    await audit.record(store, request, "pair_let_in", req["node_id"], asked_by=req["by"])
    return {"id": request_id, "status": "accepted", "token": pair["token"]}


# ── whoever holds the link ───────────────────────────────────────────────────

async def _let_in(token: str, request: Request, store: StoreDep,
                  principal: Any) -> tuple[dict, str]:  # noqa: ANN401
    """The pair, and "teammate" or "guest" — or the reason not."""
    pair = await store.pair_by_token(token)
    now = _now().isoformat()
    if pair is None or pair["closed_ts"] or pair["expires_ts"] <= now:
        raise HTTPException(404, "this pair session has ended, or the link is wrong")
    set_current_org(pair["org_id"])
    if not _opened_by_developer(pair, (await store.node_kinds()).get(pair["node_id"], {})):
        raise HTTPException(404, "this pair session has ended, or the link is wrong")
    if _org_of(principal) == pair["org_id"]:
        set_current_org(pair["org_id"])
        who = _who(principal)
        if _person(principal) and who.lower() != str(pair["created_by"]).lower() and not any(
                str(g.get("who", "")).lower() == who.lower() for g in pair["guests"]):
            # a teammate let in: the developer is told in their agent, once
            guests = [*pair["guests"], {"who": who, "ts": now, "team": True, "told": False}]
            await store.update_pair(token, guests=guests)
            pair = {**pair, "guests": guests}
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
        if sum(1 for g in guests if not g.get("team")) >= OUTSIDE_CAP:
            raise HTTPException(403, {"code": "pair_full",
                                      "detail": f"this pair session already has "
                                                f"{OUTSIDE_CAP} guests from outside the team"})
        guests = [*guests, {"who": who, "org": _org_of(principal), "ts": now, "told": False}]
        await store.update_pair(token, guests=guests)
        pair = {**pair, "guests": guests}
    set_current_org(pair["org_id"])
    return pair, "guest"


_DIFF_MAX = 1500


def _diff(tool: str, args: dict) -> dict | None:
    """An edit as the pair sees it: the text replaced and what replaced it, a
    file written, or a patch — from the call's own arguments (Claude Code's
    Edit / MultiEdit / Write, Codex's apply_patch)."""
    path = str(args.get("file_path") or args.get("path") or "")
    if isinstance(args.get("old_string"), str) and isinstance(args.get("new_string"), str):
        return {"path": path, "hunks": [{"old": args["old_string"][:_DIFF_MAX],
                                         "new": args["new_string"][:_DIFF_MAX]}]}
    if isinstance(args.get("edits"), list):
        hunks = [{"old": str(e.get("old_string", ""))[:_DIFF_MAX],
                  "new": str(e.get("new_string", ""))[:_DIFF_MAX]}
                 for e in args["edits"][:5] if isinstance(e, dict)]
        return {"path": path, "hunks": hunks} if hunks else None
    if tool in ("Write", "write_file") and isinstance(args.get("content"), str):
        return {"path": path, "hunks": [{"old": "", "new": args["content"][:_DIFF_MAX]}]}
    patch = args.get("patch") or (args.get("input") if "patch" in tool.lower() else None)
    if isinstance(patch, str) and patch.strip():
        return {"path": path, "patch": patch[: _DIFF_MAX * 2]}
    return None


def _step(event_id: int, line: dict) -> dict | None:
    """One recorded line, as the pair view shows it."""
    kind = line.get("kind")
    p = line.get("payload") or {}
    base = {"id": event_id, "ts": line.get("ts", ""), "kind": kind}
    if kind == "tool_call":
        args = p.get("args") or {}
        target = (args.get("command") or args.get("file_path") or args.get("url")
                  or args.get("pattern") or args.get("path") or args.get("skill") or "")
        diff = _diff(str(p.get("tool", "")), args) if isinstance(args, dict) else None
        return {**base, "tool": p.get("tool", ""), "target": str(target)[:300],
                "refused": line.get("verdict") == "deny",
                "reason": str(p.get("reason") or "")[:300],
                **({"diff": diff} if diff else {})}
    if kind == "tool_result":
        check = p.get("check") or None
        return {**base, "tool": p.get("tool", ""), "failed": p.get("status") == "error",
                "preview": str(p.get("preview") or "")[:300],
                "check": ({k: check.get(k) for k in ("kind", "passed", "summary")}
                          if check else None)}
    if kind == "claim":
        return {**base, "text": str(p.get("text") or "")[:2000]}
    if kind == "intent" and p.get("role") in ("developer", "assistant"):
        # the developer's request, and what the agent says on the way
        return {**base, "kind": "said", "role": p["role"], "text": str(p.get("text") or "")[:4000]}
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
                 "guests": [g["who"] for g in pair["guests"] if not g.get("team")],
                 "joined": [g["who"] for g in pair["guests"] if g.get("team")],
                 "invited": pair["invited"], "you": you,
                 # the developer holds their own agent; whoever they let in
                 # watches and writes notes
                 "yours": _person(principal) is False or str(pair["created_by"]).lower()
                 == _who(principal).lower(),
                 "paused": paused, "stopped": bool(desired and desired[1].get("stopped")),
                 "desired_version": desired[0] if desired else 0, "level": posture},
        "steps": steps,
        "cursor": rows[-1][0] if rows else after,
        "notes": await store.pair_notes_of(token),
    }


# How long the stream sleeps with nothing new before it looks again (a note
# decided, the pair closed, a plan lapsing for an outside guest).
STREAM_IDLE = 5.0


def wake(request: Request, node_id: str) -> None:
    """Tell a pair stream on this session that something changed."""
    from axor_backend.tenancy import topic

    request.app.state.broadcast.publish(topic("node", node_id), {"type": "wake"})


@router.get("/v1/pair/{token}/stream")
async def pair_stream(token: str, request: Request, store: StoreDep, principal: PrincipalDep,
                      after: int = 0,
                      last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
                      ) -> Any:  # noqa: ANN401
    """The pair feed, live: ``step`` events as the agent works (its calls, what
    it says, its edits, its answer; the developer's requests), ``notes`` when
    a note or an agent message is sent, given or decided, and ``pair`` when the
    session is held or let go. Replays from ``Last-Event-ID`` (or ``after``)."""
    import asyncio
    import json

    from sse_starlette.sse import EventSourceResponse

    from axor_backend.broadcast import messages as bus_messages
    from axor_backend.tenancy import topic

    pair, _you = await _let_in(token, request, store, principal)
    org, node = pair["org_id"], pair["node_id"]
    queue = request.app.state.broadcast.subscribe(topic("node", node, org))
    try:
        cursor = int(last_event_id) if last_event_id else max(after, 0)
    except ValueError:
        cursor = max(after, 0)

    async def stream():  # noqa: ANN202
        nonlocal cursor
        last_notes = last_pair = ""
        # the shared loop: it ends when the bus drops this reader. One wait at
        # a time that outlives the idle timeout — cancelling it would close
        # the loop, and the stream with it
        woken = bus_messages(queue)
        waiting: asyncio.Future | None = None
        try:
            while True:
                set_current_org(org)
                try:
                    await _let_in(token, request, store, principal)
                except HTTPException as exc:
                    yield {"event": "ended", "data": json.dumps({"status": exc.status_code})}
                    return
                for event_id, _run, line in await store.node_events_after(node, cursor,
                                                                          FEED_LIMIT):
                    cursor = event_id
                    if (step := _step(event_id, line)) is not None:
                        yield {"event": "step", "id": str(event_id), "data": json.dumps(step)}
                notes = json.dumps(await store.pair_notes_of(token))
                if notes != last_notes:
                    last_notes = notes
                    yield {"event": "notes", "id": str(cursor), "data": notes}
                desired = await store.get_desired(node)
                state = json.dumps({"paused": bool(desired and desired[1].get("paused")),
                                    "stopped": bool(desired and desired[1].get("stopped")),
                                    "desired_version": desired[0] if desired else 0})
                if state != last_pair:
                    last_pair = state
                    yield {"event": "pair", "id": str(cursor), "data": state}
                if waiting is None:
                    waiting = asyncio.ensure_future(anext(woken))
                done, _ = await asyncio.wait({waiting}, timeout=STREAM_IDLE)
                if not done:
                    continue
                if isinstance(waiting.exception(), StopAsyncIteration):
                    return
                waiting = None
        finally:
            if waiting is not None:
                waiting.cancel()
            request.app.state.broadcast.unsubscribe(topic("node", node, org), queue)

    return EventSourceResponse(stream())


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
    signed = _signed_note(body, pair["node_id"], text) if you != "guest" else None
    note_id = await store.add_pair_note(token, pair["node_id"], by, text, _now().isoformat(),
                                        outside=you == "guest", signed=signed)
    wake(request, pair["node_id"])
    from axor_backend.funnel import used

    await used(store, "pair_note")
    from axor_backend import audit

    await audit.record(store, request, "pair_note", pair["node_id"], who=by,
                       text=audit.first_words(text), **({"guest": True} if you == "guest" else {}))
    return {"id": note_id}


# ── the developer's sidecar ──────────────────────────────────────────────────

def _speaks_for(principal: Any, node_id: str) -> None:  # noqa: ANN401
    if principal is not None and not principal.may_speak_for(node_id):
        raise HTTPException(403, "this key is bound to another node")


@router.get("/v1/coding/notes/{node_id}")
async def notes_for_agent(node_id: str, store: StoreDep, principal: PrincipalDep) -> dict:
    _speaks_for(principal, node_id)
    since = (_now() - NOTE_KEEP).isoformat()
    # said to the developer in their own agent, once each: who joined a pair
    # on this session, and who asks to
    news = [f"{who} joined a pair session on this session — they see your agent's "
            f"steps live and can send it notes. End it on the Control Plane, Pair sessions."
            for who in await store.untold_joins(node_id)]
    news += [f"{r['by']} asks to pair on this session (watch it live, send your agent "
             f"notes). Let them in or not on the Control Plane, Pair sessions."
             for r in await store.untold_pair_requests(node_id)]
    news += [f"the agent of another session ({h['from_node']}) wants to tell your agent: "
             f"\"{str(h['text'])[:200]}\" — send it on or dismiss it on the Control Plane "
             f"(your session's notes). Messages from teammates' agents wait for your OK; "
             f"switch to auto there to let them through."
             for h in await store.untold_held(node_id)]
    developer = str(((await store.node_kinds()).get(node_id) or {}).get("developer") or "").lower()
    notes = await store.pending_notes(node_id, since)
    for n in notes:
        # the developer's own words, from the plane's session panel or a phone
        n["own"] = bool(developer) and str(n.get("by", "")).lower() == developer
    return {"notes": notes, "news": news}


# ── the developer, to their own agent ────────────────────────────────────────

@router.post("/v1/coding/say/{node_id}", status_code=201)
async def say_to_agent(node_id: str, body: dict, request: Request, store: StoreDep,
                       principal: PrincipalDep) -> dict:
    """A note from the session's developer to their own agent — from the
    plane's session panel, or a phone while a long run goes on. It reaches the
    agent at its next step, like the notes of a pair, as the developer's words."""
    profile = (await store.node_kinds()).get(node_id)
    if profile is None or profile.get("kind") != "coding":
        raise HTTPException(404, "no coding session by that id in this workspace")
    if not _mine(principal, profile):
        raise HTTPException(403, {"code": "not_your_session",
                                  "detail": f"this is {profile.get('developer')}'s session — "
                                            f"only they speak to its agent; ask to pair to "
                                            f"send it a note"})
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "a note has text")
    if len(text) > NOTE_MAX:
        raise HTTPException(413, f"a note is at most {NOTE_MAX} characters")
    by = _who(principal) or str(profile.get("developer") or "the developer")
    note_id = await store.add_pair_note("", node_id, by, text, _now().isoformat(),
                                        signed=_signed_note(body, node_id, text))
    wake(request, node_id)
    from axor_backend.funnel import used

    await used(store, "say")
    from axor_backend import audit

    await audit.record(store, request, "say", node_id, text=audit.first_words(text))
    return {"id": note_id}


@router.get("/v1/coding/say/{node_id}")
async def notes_to_agent(node_id: str, store: StoreDep, principal: PrincipalDep) -> dict:
    """The latest notes to this session and where each landed — the
    developer's panel. Someone else's session: its developer's to read."""
    profile = (await store.node_kinds()).get(node_id) or {}
    if not _mine(principal, profile):
        raise not_yours(profile)
    return {"notes": await store.notes_of_node(node_id)}


@router.post("/v1/coding/notes/{node_id}/delivered")
async def notes_delivered(node_id: str, body: dict, request: Request, store: StoreDep,
                          principal: PrincipalDep) -> dict:
    _speaks_for(principal, node_id)
    ids = [int(i) for i in body.get("ids") or [] if isinstance(i, int)]
    n = await store.mark_notes_delivered(node_id, ids, _now().isoformat(),
                                         str(body.get("at") or ""))
    wake(request, node_id)
    return {"delivered": n}


# ── agents of a pair, to each other ──────────────────────────────────────────
#
# A session's agent may write to a PEER session's agent: another session of the
# same developer, or a session joined by a pair (the developer of one let the
# developer of the other in, either way round). Provider-blind: a Claude Code
# agent writes to a Codex one. The sender's sidecar says what its taint ledger
# found in the text (TaintSource values), and the receiver's sidecar registers
# the text with that origin — a message built from a web page arrives as web
# content, wherever the receiving agent runs.
#
# Between two sessions of one developer a message is given at once. To someone
# else's agent it goes by that developer's MODE: "manual" (the default) holds
# it until they send or dismiss it, "auto" gives it at once.

AGENT_MSG_MODES = ("manual", "auto")
_TAINT_MAX = 8
_LIVE = timedelta(minutes=10)


def _mode_key(developer: str) -> str:
    return f"agent_msg_mode:{developer.lower()}"


async def agent_msg_mode(store: Any, developer: str) -> str:  # noqa: ANN401
    value = await store.get_setting(_mode_key(developer)) if developer else None
    return value if value in AGENT_MSG_MODES else "manual"


def _is_developer(developer: str, email: str) -> bool:
    """Whether a session's developer is this person. ``node_kinds`` already
    reads a machine's opaque id as the person it is bound to
    (``developer_identities``), so this is the one comparison."""
    return bool(developer) and developer.strip().lower() == email.strip().lower()


async def peers_of(store: Any, node_id: str) -> list[dict]:  # noqa: ANN401
    """The sessions this session's agent may write to, each with why."""
    kinds = await store.node_kinds()
    me = kinds.get(node_id)
    if me is None or me.get("kind") != "coding":
        return []
    dev = str(me.get("developer") or "").lower()
    found: dict[str, dict] = {}
    for other, prof in kinds.items():
        if other != node_id and prof.get("kind") == "coding" and not prof.get("parent") \
                and dev and str(prof.get("developer") or "").lower() == dev:
            found[other] = {"relation": "own", "token": ""}
    now = _now()
    for pair in await store.list_pairs(now.isoformat()):
        host = kinds.get(pair["node_id"]) or {}
        if not _opened_by_developer(pair, host):
            continue
        people = {str(pair["created_by"]).lower(), *[str(i).lower() for i in pair["invited"]],
                  *[str(g.get("who", "")).lower() for g in pair["guests"] if g.get("team")]}
        if pair["node_id"] == node_id:
            # whoever is in a pair on THIS session: their own sessions
            for other, prof in kinds.items():
                if other != node_id and prof.get("kind") == "coding" and not prof.get("parent") \
                        and other not in found and any(
                        _is_developer(str(prof.get("developer") or ""), p) for p in people):
                    found[other] = {"relation": "pair", "token": pair["token"]}
        elif pair["node_id"] not in found and any(_is_developer(dev, p) for p in people):
            # this session's developer is in a pair on that session
            found[pair["node_id"]] = {"relation": "pair", "token": pair["token"]}
    reported = await store.all_reported()
    live_after = (now - _LIVE).isoformat()
    out = []
    for other, why in found.items():
        prof = kinds.get(other) or {}
        seen = str((reported.get(other) or {}).get("updated_ts") or "")
        out.append({"node_id": other, "agent": prof.get("agent", ""),
                    "title": prof.get("title", ""), "repo": prof.get("repo_label")
                    or prof.get("repo", ""), "relation": why["relation"],
                    "token": why["token"], "live": bool(seen) and seen > live_after,
                    **({"developer": prof["developer"]}
                       if "@" in str(prof.get("developer") or "") else {})})
    out.sort(key=lambda p: (not p["live"], p["relation"] != "own", p["node_id"]))
    return out[:20]


@router.get("/v1/coding/peers/{node_id}")
async def peers(node_id: str, store: StoreDep, principal: PrincipalDep) -> dict:
    """For the sidecar: whom this session's agent may write to."""
    _speaks_for(principal, node_id)
    return {"peers": [{k: v for k, v in p.items() if k != "token"}
                      for p in await peers_of(store, node_id)]}


@router.post("/v1/coding/notes/{node_id}/send", status_code=201)
async def agent_send(node_id: str, body: dict, request: Request, store: StoreDep,
                     principal: PrincipalDep) -> dict:
    """The sidecar of ``node_id`` sends its agent's message to a peer's agent."""
    _speaks_for(principal, node_id)
    to = str(body.get("to", ""))
    text = str(body.get("text", "")).strip()
    if not text:
        raise HTTPException(400, "a message has text")
    if len(text) > NOTE_MAX:
        raise HTTPException(413, f"a message is at most {NOTE_MAX} characters")
    peer = next((p for p in await peers_of(store, node_id) if p["node_id"] == to), None)
    if peer is None:
        raise HTTPException(403, {"code": "not_a_peer",
                                  "detail": "that session is not one this agent may write to: "
                                            "another session of the same developer, or one "
                                            "joined by an open pair session"})
    taint = sorted({str(t)[:32] for t in body.get("taint") or [] if str(t).strip()})[:_TAINT_MAX]
    kinds = await store.node_kinds()
    receiver = str((kinds.get(to) or {}).get("developer") or "")
    held = peer["relation"] != "own" and await agent_msg_mode(store, receiver) == "manual"
    title = str((kinds.get(node_id) or {}).get("title") or "")
    by = f"agent:{node_id}" + (f" ({title[:60]})" if title else "")
    note_id = await store.add_pair_note(peer["token"], to, by, text, _now().isoformat(),
                                        from_node=node_id, taint=taint, held=held)
    wake(request, to)
    wake(request, node_id)
    from axor_backend.funnel import used

    await used(store, "agent_message")
    from axor_backend import audit

    await audit.record(store, request, "agent_message", to, who=f"agent:{node_id}",
                       text=audit.first_words(text), relation=peer["relation"],
                       **({"taint": taint} if taint else {}), **({"held": True} if held else {}))
    return {"id": note_id, "to": to, "relation": peer["relation"],
            "status": "held" if held else "queued"}


@router.get("/v1/coding/notes/{node_id}/with/{peer}")
async def agent_thread(node_id: str, peer: str, store: StoreDep,
                       principal: PrincipalDep) -> dict:
    """For the sidecar: the latest messages either way between two sessions."""
    _speaks_for(principal, node_id)
    if not any(p["node_id"] == peer for p in await peers_of(store, node_id)):
        raise HTTPException(403, {"code": "not_a_peer", "detail": "not a peer of this session"})
    return {"messages": [
        {k: m[k] for k in ("id", "from_node", "node_id", "text", "ts", "held", "dismissed_ts",
                           "delivered_ts", "taint")}
        for m in await store.agent_messages_between(node_id, peer)]}


@router.post("/v1/coding/say/{node_id}/held/{note_id}")
async def decide_held(node_id: str, note_id: int, body: dict, request: Request,
                      store: StoreDep, principal: PrincipalDep) -> dict:
    """The receiving developer sends a held agent message on to their agent, or
    dismisses it."""
    profile = (await store.node_kinds()).get(node_id) or {}
    if not _mine(principal, profile):
        raise not_yours(profile)
    note = await store.pair_note(note_id)
    if note is None or note["node_id"] != node_id:
        raise HTTPException(404, "no such message to this session")
    send = bool(body.get("send"))
    if not await store.decide_held_note(note_id, send, _now().isoformat()):
        raise HTTPException(409, "this message is not waiting any more")
    wake(request, node_id)
    from axor_backend import audit

    await audit.record(store, request, "agent_message_sent" if send else
                       "agent_message_dismissed", node_id, from_node=note["from_node"])
    return {"id": note_id, "status": "queued" if send else "dismissed"}


@router.get("/v1/coding/say/{node_id}/mode")
async def get_mode(node_id: str, store: StoreDep, principal: PrincipalDep) -> dict:
    profile = (await store.node_kinds()).get(node_id) or {}
    if not _mine(principal, profile):
        raise not_yours(profile)
    return {"mode": await agent_msg_mode(store, str(profile.get("developer") or ""))}


@router.put("/v1/coding/say/{node_id}/mode")
async def put_mode(node_id: str, body: dict, request: Request, store: StoreDep,
                   principal: PrincipalDep) -> dict:
    """How messages from other developers' agents reach this developer's
    agents: held for their OK ("manual") or given at once ("auto")."""
    profile = (await store.node_kinds()).get(node_id) or {}
    if not _mine(principal, profile):
        raise not_yours(profile)
    mode = str(body.get("mode", ""))
    if mode not in AGENT_MSG_MODES:
        raise HTTPException(400, f"mode is one of {', '.join(AGENT_MSG_MODES)}")
    developer = str(profile.get("developer") or "")
    if not developer:
        raise HTTPException(409, "this session names no developer")
    await store.set_setting(_mode_key(developer), mode)
    from axor_backend import audit

    await audit.record(store, request, "agent_message_mode", node_id, mode=mode)
    return {"mode": mode}
