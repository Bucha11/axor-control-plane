"""The Incidents feed (incidents.py) and the status an operator gives an item."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from axor_backend import incidents
from axor_backend.clock import now
from axor_backend.deps import StoreDep
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
    items = await incidents.incidents(store, SEARCH_SCAN)
    new = {k: sum(1 for i in items if i["kind"] == k and i["status"] == "new")
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
async def set_status(body: dict, store: StoreDep) -> dict:
    item, status = body.get("id"), body.get("status")
    if not isinstance(item, str) or not item or len(item) > 240:
        raise HTTPException(400, "id names an item of the feed")
    if status not in incidents.STATUSES:
        raise HTTPException(400, f"status is one of {', '.join(incidents.STATUSES)}")
    await store.set_incident_status(item, status, now())
    return {"id": item, "status": status}
