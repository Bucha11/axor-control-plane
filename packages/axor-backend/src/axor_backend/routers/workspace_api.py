"""What this workspace is for — coding agents, production agents, or both.

The menu, the onboarding and the empty states follow it. It used to live in
one browser's storage, so a teammate who joined by invitation landed on the
production screens of a coding team and was asked again what they wanted to
govern. It is the workspace's now: set by whoever chose it first, and until
anyone has, read from what reports here.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from axor_backend.deps import StoreDep
from axor_backend.examples import work_started

router = APIRouter(prefix="/v1")

SETTING = "workspace_journey"
JOURNEYS = ("coding", "production", "both")


async def journey_of(store: object) -> str | None:
    chosen = await store.get_setting(SETTING)  # type: ignore[attr-defined]
    if chosen in JOURNEYS:
        return str(chosen)
    kinds = {p["kind"] for p in (await store.node_kinds()).values()}  # type: ignore[attr-defined]
    coding = bool(kinds & {"coding", "tree"})
    production = bool(kinds - {"coding", "tree", ""})
    if coding and production:
        return "both"
    return "coding" if coding else "production" if production else None


@router.get("/workspace")
async def get_workspace(store: StoreDep) -> dict:
    # work_started: the workspace's own agents have reported, so the screens
    # (and the tour) show them instead of the product's examples
    return {"journey": await journey_of(store),
            "work_started": await work_started(store) is not None}


@router.put("/workspace/journey")
async def set_journey(body: dict, store: StoreDep) -> dict:
    journey = body.get("journey")
    if journey not in JOURNEYS:
        raise HTTPException(400, f"journey must be one of {list(JOURNEYS)}")
    await store.set_setting(SETTING, journey)
    return {"journey": journey}
