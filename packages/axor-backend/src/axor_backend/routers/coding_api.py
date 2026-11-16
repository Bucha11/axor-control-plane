"""Coding agents, governed from the plane: skills turned off for the team.

A skill (``.claude/skills/<name>`` / ``.codex/skills/<name>``) is turned off
here for every developer of the organization. The plane cannot touch a
developer's disk; what it can do is tell every axor sidecar, which refuses the
agent's use of it — the ``Skill`` call naming it, and any read, run or edit of
a file under its folder. The skill's name and description stay in the agent's
context (the agent loads them at start) until the folder is removed; the
screen says so.

- ``GET  /v1/coding/policy``       what the sidecars fetch (an ingest key reads it)
- ``GET  /v1/coding/skills``       the list for the screen, with recent refusals
- ``PUT  /v1/coding/skills/{name}`` ``{"off": true|false}`` — operate
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from axor_backend.deps import PrincipalDep

router = APIRouter(prefix="/v1/coding", tags=["coding"])

SKILLS_OFF = "coding_skills_off"
_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# the reason the sidecar gives (axor_wrap.agents.rules.skill_refusal)
_REFUSED = re.compile(r"the skill '([^']+)' is turned off from the Control Plane")


async def _off(request: Request) -> dict[str, dict]:
    value = await request.app.state.store.get_setting(SKILLS_OFF)
    return value if isinstance(value, dict) else {}


@router.get("/policy")
async def policy(request: Request) -> dict:
    """For the sidecars: the names, nothing else."""
    return {"skills_off": sorted(await _off(request))}


@router.get("/skills")
async def skills(request: Request) -> dict:
    off = await _off(request)
    attempts: Counter[str] = Counter()
    for row in await request.app.state.store.recent_refusals():
        m = _REFUSED.search(str(row["payload"].get("reason", "")))
        if m:
            attempts[m.group(1)] += 1
    return {"off": [{"skill": k, **v} for k, v in sorted(off.items())],
            "attempts": dict(attempts)}


@router.put("/skills/{name}")
async def set_skill(name: str, body: dict, request: Request, principal: PrincipalDep) -> dict:
    if not _NAME.match(name):
        raise HTTPException(400, "a skill name is a plain id (letters, digits, - _ . :)")
    if not isinstance(body.get("off"), bool):
        raise HTTPException(400, 'body is {"off": true} or {"off": false}')
    off = await _off(request)
    if body["off"]:
        by = (principal.email or principal.key_id) if principal is not None else ""
        off[name] = {"ts": datetime.now(UTC).isoformat(), "by": by or ""}
    else:
        off.pop(name, None)
    await request.app.state.store.set_setting(SKILLS_OFF, off)
    return {"skills_off": sorted(off)}
