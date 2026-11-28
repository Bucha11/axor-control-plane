"""The Incidents feed (incidents.py) and the status an operator gives an item."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from axor_backend import incidents
from axor_backend.clock import now
from axor_backend.deps import StoreDep
from axor_backend.examples import is_example_node, without_examples
from axor_backend.routers.runs import SEARCH_SCAN

router = APIRouter(prefix="/v1/incidents", tags=["incidents"])


def _when(ts: object) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


@router.get("")
async def list_incidents(store: StoreDep, kind: str = "", status: str = "", agent: str = "",
                         since: str = "") -> dict:
    # the product's examples stop counting once the workspace's own agents report
    items = await without_examples(store, await incidents.incidents(store, SEARCH_SCAN))
    # the menu's count is the workspace's own: an example opened (the memory
    # example, the tour) lit it with the example's incidents (QA-440, QA-466)
    new = {k: sum(1 for i in items if i["kind"] == k and i["status"] == "new"
                  and not is_example_node(str(i.get("node_id") or "")))
           for k in incidents.KINDS}
    lo = _when(since) if since else None
    if lo is not None:
        items = [i for i in items if (w := _when(i["ts"])) is None or w >= lo]
    if kind:
        wanted = set(kind.split(","))
        items = [i for i in items if i["kind"] in wanted]
    if status:
        wanted = set(status.split(","))
        items = [i for i in items if i["status"] in wanted]
    if agent:
        items = [i for i in items if agent.lower() in i["node_id"].lower()]
    return {"items": items[:500], "new": new,
            "needs_a_look": sum(new[k] for k in incidents.NEEDS_A_LOOK)}


@router.put("/status")
async def set_status(body: dict, request: Request, store: StoreDep) -> dict:
    item, status = body.get("id"), body.get("status")
    if not isinstance(item, str) or not item or len(item) > 240:
        raise HTTPException(400, "id names an item of the feed")
    if status not in incidents.STATUSES:
        raise HTTPException(400, f"status is one of {', '.join(incidents.STATUSES)}")
    await store.set_incident_status(item, status, now())
    from axor_backend import audit

    await audit.record(store, request, f"incident_{status}", item)
    return {"id": item, "status": status}


MAX_MUTES = 200


@router.get("/mute")
async def list_mutes(store: StoreDep) -> dict:
    value = await store.get_setting(incidents.MUTED)
    return {"muted": value if isinstance(value, list) else []}


@router.put("/mute")
async def set_mute(body: dict, request: Request, store: StoreDep) -> dict:
    """"Not a problem — don't flag this again": a let-through call of this kind,
    what and detail (``*`` as a wildcard in the detail) stops being an item.
    ``{"on": false}`` lifts it."""
    kind, what = body.get("kind"), body.get("what")
    detail = " ".join(str(body.get("detail") or "").split())
    if kind not in ("dangerous", "would_refuse") or not isinstance(what, str) or not what:
        raise HTTPException(400, "only a dangerous or would-refuse item is muted, by kind and what")
    if not detail or detail.strip("* ") == "" or len(detail) > 300:
        raise HTTPException(400, "detail names the call — not everything (*)")
    held = await store.get_setting(incidents.MUTED)
    mutes = [m for m in (held if isinstance(held, list) else [])
             if (m.get("kind"), m.get("what"), m.get("detail")) != (kind, what, detail)]
    if body.get("on", True):
        if len(mutes) >= MAX_MUTES:
            raise HTTPException(400, f"at most {MAX_MUTES} muted calls")
        mutes.append({"kind": kind, "what": what, "detail": detail, "ts": now()})
    await store.set_setting(incidents.MUTED, mutes)
    from axor_backend import audit

    await audit.record(store, request, "muted" if body.get("on", True) else "unmuted",
                       f"{what}: {detail}", kind=kind)
    return {"muted": mutes}
