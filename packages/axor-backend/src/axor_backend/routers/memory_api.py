"""The team's memory (memory.py), for the sidecars and for the screen.

- ``GET  /v1/coding/memory?repo=``   what a sidecar gives its agent at the start
  of a session: the text and the items in it (an ingest key reads it)
- ``POST /v1/coding/memory/given``   which session was given which items
- ``GET  /v1/memory``                every repository's memory, for the screen
- ``PUT  /v1/memory/{id}``           ``{"status": "on"|"off"}`` — operate
- ``POST /v1/memory/notes``          ``{"repo", "text"}`` a person's note, on at
  once — operate (it reaches every agent of the team)
- ``PUT  /v1/memory/settings``       ``{"fresh_days"}`` — operate
- ``GET  /v1/memory/session/{node}`` what one session was given (Replay)

Every read rotates first and logs a change of what agents get (memory.refresh),
so the version a session is given is always the logged one.
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from axor_backend import memory
from axor_backend.deps import PrincipalDep, StoreDep

router = APIRouter(tags=["memory"])

NOTE_MAX = 400


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _who(principal: object) -> str:
    if principal is None:
        return ""
    return str(getattr(principal, "email", "") or getattr(principal, "key_id", "") or "")


@router.get("/v1/coding/memory")
async def memory_for_agent(repo: str, request: Request, store: StoreDep) -> dict:
    given = memory.given_on_plan(request.app.state, repo)
    items, text, ids, version = await memory.refresh(store, repo, given=given)
    by_id = {i["id"]: i for i in items}
    # each fact on its own too: a setup health check takes them out one by one
    return {"repo": repo, "label": (await store.repo_labels()).get(repo, ""), "text": text,
            "ids": ids, "version": version,
            "items": [{"id": i, "text": by_id[i]["doc"]["text"], "kind": by_id[i]["kind"]}
                      for i in ids if i in by_id],
            **({} if given else {"plan_required": memory.PLAN_TIER})}


@router.post("/v1/coding/memory/given")
async def memory_given(body: dict, store: StoreDep, principal: PrincipalDep) -> dict:
    node_id, repo = str(body.get("node_id", "")), str(body.get("repo", ""))
    if not node_id or not repo:
        raise HTTPException(400, "body is {node_id, repo, ids}")
    if principal is not None and not principal.may_speak_for(node_id):
        raise HTTPException(403, "this key is bound to another node")
    ids = [int(i) for i in body.get("ids") or [] if isinstance(i, int)][:100]
    ts = _now()
    # given nothing is recorded too: it is the "before" of the first change
    await store.add_memory_given(repo, node_id, ids, ts, memory.version_of(ids))
    for item_id in ids:
        item = await store.memory_item_by_id(item_id)
        if item and item["repo"] == repo:
            doc = {**item["doc"], "given": item["doc"].get("given", 0) + 1, "last_given_ts": ts}
            await store.update_memory_item(item_id, item["status"], doc, item["updated_ts"])
    return {"recorded": len(ids)}


COMPARE_CHANGES = 5


@router.get("/v1/memory")
async def team_memory(request: Request, store: StoreDep) -> dict:
    labels = await store.repo_labels()
    repos = sorted({i["repo"] for i in await store.memory_items_of()} | set(labels))
    out = []
    for repo in repos:
        given = memory.given_on_plan(request.app.state, repo)
        items, text, ids, version = await memory.refresh(store, repo, labels.get(repo, ""),
                                                         given=given)
        if not given:
            # what agents WOULD get: the screen shows it, marked as not given
            text, ids = memory.for_agent(items, labels.get(repo, ""))
        changes = await store.memory_changes_of(repo, 10)
        for n, change in enumerate(changes):
            change["compare"] = (await memory.compare(store, repo, change)
                                 if n < COMPARE_CHANGES else None)
        out.append({"repo": repo, "label": labels.get(repo, repo), "items": items,
                    "preview": text, "given_ids": ids, "version": version, "given": given,
                    "sessions_given": await store.memory_sessions_given(repo),
                    "changes": changes,
                    # worse after a change, while what it added is still given —
                    # turning the culprit off settles it
                    "drift": next((c for c in changes[:COMPARE_CHANGES]
                                   if (c.get("compare") or {}).get("verdict") == "worse"
                                   and (any(a["id"] in ids for a in c["added"])
                                        or not c["added"])), None)})
    return {"repos": out, "fresh_days": await memory.fresh_days(store),
            "plan_tier": memory.PLAN_TIER}


@router.put("/v1/memory/settings")
async def memory_settings(body: dict, store: StoreDep) -> dict:
    days = body.get("fresh_days")
    if not isinstance(days, int) or not 1 <= days <= 365:
        raise HTTPException(400, "fresh_days is a whole number of days, 1 to 365")
    await store.set_setting(memory.FRESH_SETTING, days)
    return {"fresh_days": days}


@router.get("/v1/memory/session/{node_id}")
async def memory_of_session(node_id: str, store: StoreDep) -> dict:
    """What one session was given, item by item (as the items read now)."""
    given = await store.memory_given_to(node_id)
    out = []
    for g in given:
        texts = []
        for i in g["ids"]:
            item = await store.memory_item_by_id(i)
            texts.append({"id": i, "text": item["doc"]["text"] if item else "(no longer kept)",
                          "kind": item["kind"] if item else ""})
        out.append({**g, "items": texts})
    return {"node_id": node_id, "given": out}


@router.put("/v1/memory/{item_id}")
async def set_status(item_id: int, body: dict, request: Request, store: StoreDep,
                     principal: PrincipalDep) -> dict:
    status = body.get("status")
    if status not in ("on", "off"):
        raise HTTPException(400, 'body is {"status": "on"} or {"status": "off"}')
    item = await store.memory_item_by_id(item_id)
    if item is None:
        raise HTTPException(404, "no such memory item")
    if item["status"] == "kept_out" and status == "on":
        raise HTTPException(409, f"kept out: {item['doc'].get('reason', '')}")
    now = _now()
    doc = {**item["doc"], "decided_by": _who(principal), "decided_ts": now, "auto": False}
    if status == "on":
        # a person vouches for it now: fresh for another fresh_days, kept even
        # if a newer command of its kind replaced it; a note: "still true"
        doc["vouched_ts"] = now
        if item["status"] == "replaced":
            doc["keep"] = True
    else:
        doc.pop("keep", None)
    await store.update_memory_item(item_id, status, doc, now)
    await memory.refresh(store, item["repo"],
                         given=memory.given_on_plan(request.app.state, item["repo"]))
    return {"id": item_id, "status": status}


@router.post("/v1/memory/notes", status_code=201)
async def add_note(body: dict, request: Request, store: StoreDep,
                   principal: PrincipalDep) -> dict:
    repo = str(body.get("repo", ""))
    text = " ".join(str(body.get("text", "")).split())
    if not repo or repo not in await store.repo_labels():
        raise HTTPException(404, "no coding session has reported that repository")
    if not text:
        raise HTTPException(400, "a note has text")
    if len(text) > NOTE_MAX:
        raise HTTPException(413, f"a note is at most {NOTE_MAX} characters — it goes into "
                                 "every agent's context")
    ts = _now()
    doc = {"text": text, "by": _who(principal), "developers": [], "evidence": [], "fails": 0,
           "last_ok_ts": ts, "given": 0, "auto": False, "reason": ""}
    item_id = await store.add_memory_item(repo, f"note:{ts}:{text[:200]}", "note", "on", doc, ts)
    await memory.refresh(store, repo, given=memory.given_on_plan(request.app.state, repo))
    return {"id": item_id}
