"""Who may do what to the team's agents (axor_backend.permissions): read by
anyone signed in — with what *they* may, so a screen offers only what will be
accepted — and set by an admin, each change on the audit trail."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from axor_backend import audit, permissions
from axor_backend.deps import PrincipalDep, StoreDep

router = APIRouter(prefix="/v1/permissions", tags=["permissions"])

MAX_PEOPLE = 200


def _view(grants: dict, principal: object) -> dict:
    return {"capabilities": {cap: {"what": what, **grants[cap]}
                             for cap, (what, _) in permissions.CAPABILITIES.items()},
            "you": permissions.mine(grants, principal)}


@router.get("")
async def get_permissions(store: StoreDep, principal: PrincipalDep) -> dict:
    return _view(await permissions.held(store), principal)


@router.put("")
async def set_permissions(body: dict, request: Request, store: StoreDep,
                          principal: PrincipalDep) -> dict:
    """``{capability: {"members": bool, "people": [emails]}}`` — the ones named
    change, the rest stay as they were. An owner or admin."""
    permissions.require_admin(principal)
    if not isinstance(body, dict) or not body:
        raise HTTPException(400, "name a capability: {capability: {members, people}}")
    held = await permissions.held(store)
    after = {cap: dict(v) for cap, v in held.items()}
    for cap, change in body.items():
        if cap not in permissions.CAPABILITIES:
            raise HTTPException(400, f"capabilities are {sorted(permissions.CAPABILITIES)}")
        if not isinstance(change, dict):
            raise HTTPException(400, f"{cap} is {{members: bool, people: [emails]}}")
        if "members" in change:
            if not isinstance(change["members"], bool):
                raise HTTPException(400, "members is true or false")
            after[cap]["members"] = change["members"]
        if "people" in change:
            people = change["people"]
            if not isinstance(people, list) or len(people) > MAX_PEOPLE or not all(
                    isinstance(p, str) and "@" in p and len(p) <= 254 for p in people):
                raise HTTPException(400, f"people is a list of at most {MAX_PEOPLE} emails")
            after[cap]["people"] = sorted({p.strip().lower() for p in people})
    await store.set_setting(permissions.SETTING, after)
    for cap in permissions.CAPABILITIES:
        was, now = held[cap], after[cap]
        if was["members"] != now["members"]:
            await audit.record(store, request, "permission_granted" if now["members"]
                               else "permission_withdrawn", cap, to="members")
        for p in sorted(set(now["people"]) - set(was["people"])):
            await audit.record(store, request, "permission_granted", cap, to=p)
        for p in sorted(set(was["people"]) - set(now["people"])):
            await audit.record(store, request, "permission_withdrawn", cap, to=p)
    return _view(after, principal)
