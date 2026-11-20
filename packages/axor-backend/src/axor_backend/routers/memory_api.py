"""The team's memory (memory.py), for the sidecars and for the screen.

- ``GET  /v1/coding/memory?repo=``   what a sidecar gives its agent at the start
  of a session: the text and the items in it (an ingest key reads it)
- ``POST /v1/coding/memory/given``   which session was given which items
- ``GET  /v1/memory``                every repository's memory, for the screen
- ``PUT  /v1/memory/{id}``           ``{"status": "on"|"off"}`` — operate
- ``POST /v1/memory/notes``          ``{"repo", "text"}`` a person's note, on at
  once — operate (it reaches every agent of the team)
"""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

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
async def memory_for_agent(repo: str, store: StoreDep) -> dict:
    labels = await store.repo_labels()
    text, ids = memory.for_agent(await store.memory_items_of(repo), labels.get(repo, ""))
    return {"repo": repo, "text": text, "ids": ids}


@router.post("/v1/coding/memory/given")
async def memory_given(body: dict, store: StoreDep, principal: PrincipalDep) -> dict:
    node_id, repo = str(body.get("node_id", "")), str(body.get("repo", ""))
    if not node_id or not repo:
        raise HTTPException(400, "body is {node_id, repo, ids}")
    if principal is not None and not principal.may_speak_for(node_id):
        raise HTTPException(403, "this key is bound to another node")
    ids = [int(i) for i in body.get("ids") or [] if isinstance(i, int)][:100]
    ts = _now()
    await store.add_memory_given(repo, node_id, ids, ts)
    for item_id in ids:
        item = await store.memory_item_by_id(item_id)
        if item and item["repo"] == repo:
            doc = {**item["doc"], "given": item["doc"].get("given", 0) + 1, "last_given_ts": ts}
            await store.update_memory_item(item_id, item["status"], doc, item["updated_ts"])
    return {"recorded": len(ids)}


@router.get("/v1/memory")
async def team_memory(store: StoreDep) -> dict:
    labels = await store.repo_labels()
    items = await store.memory_items_of()
    repos = sorted({i["repo"] for i in items} | set(labels))
    out = []
    for repo in repos:
        mine = [i for i in items if i["repo"] == repo]
        text, ids = memory.for_agent(mine, labels.get(repo, ""))
        out.append({"repo": repo, "label": labels.get(repo, repo), "items": mine,
                    "preview": text, "given_ids": ids,
                    "sessions_given": await store.memory_sessions_given(repo)})
    return {"repos": out}


@router.put("/v1/memory/{item_id}")
async def set_status(item_id: int, body: dict, store: StoreDep, principal: PrincipalDep) -> dict:
    status = body.get("status")
    if status not in ("on", "off"):
        raise HTTPException(400, 'body is {"status": "on"} or {"status": "off"}')
    item = await store.memory_item_by_id(item_id)
    if item is None:
        raise HTTPException(404, "no such memory item")
    if item["status"] == "kept_out" and status == "on":
        raise HTTPException(409, f"kept out: {item['doc'].get('reason', '')}")
    doc = {**item["doc"], "decided_by": _who(principal), "decided_ts": _now(), "auto": False}
    await store.update_memory_item(item_id, status, doc, _now())
    return {"id": item_id, "status": status}


@router.post("/v1/memory/notes", status_code=201)
async def add_note(body: dict, store: StoreDep, principal: PrincipalDep) -> dict:
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
    return {"id": item_id}
