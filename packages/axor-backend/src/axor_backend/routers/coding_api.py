"""Coding agents, governed from the plane: skills turned off — and on — for
the team.

A skill (``.claude/skills/<name>`` / ``.codex/skills/<name>``) is turned off
here for every developer of the organization. The plane cannot touch a
developer's disk; what it can do is tell every axor sidecar, which refuses the
agent's use of it — the ``Skill`` call naming it, and any read, run or edit of
a file under its folder. The skill's name and description stay in the agent's
context (the agent loads them at start) until the folder is removed; the
screen says so.

- ``GET  /v1/coding/policy``       what the sidecars fetch (an ingest key reads it)
- ``GET  /v1/coding/skills``       the list for the screen, with recent refusals
- ``PUT  /v1/coding/skills/{name}`` ``{"off": true|false}`` or
  ``{"team": true|false}`` — operate
- ``POST /v1/coding/skill-bundles``  a skill's files, shared for the team to
  turn on (``axor-wrap skill share``; an ingest key)
- ``GET  /v1/coding/skill-bundles/{name}`` the version turned on, files and
  all — what a sidecar installs into the agent's skills folder

A shared skill reaches nobody until someone turns it on, and a new upload of
it reaches nobody until it is turned on again: the version on for the team is
kept apart from the latest upload. Turning a skill off for the team also takes
it off the team, so the sidecars remove what they installed.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

from axor_backend.deps import PrincipalDep, StoreDep

router = APIRouter(prefix="/v1/coding", tags=["coding"])

SKILLS_OFF = "coding_skills_off"
_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
# the reason the sidecar gives (axor_wrap.agents.rules.skill_refusal)
_REFUSED = re.compile(r"the skill '([^']+)' is turned off from the Control Plane")


async def _off(request: Request) -> dict[str, dict]:
    value = await request.app.state.store.get_setting(SKILLS_OFF)
    return value if isinstance(value, dict) else {}


@router.get("/policy")
async def policy(request: Request, store: StoreDep) -> dict:
    """For the sidecars: the names (and the team's versions), nothing else."""
    team = [{"name": t["name"], "version": t["live"]["version"]}
            for t in await store.team_skills() if t["live"]]
    return {"skills_off": sorted(await _off(request)), "skills_team": team}


@router.get("/skills")
async def skills(request: Request) -> dict:
    off = await _off(request)
    attempts: Counter[str] = Counter()
    for row in await request.app.state.store.recent_refusals():
        m = _REFUSED.search(str(row["payload"].get("reason", "")))
        if m:
            attempts[m.group(1)] += 1
    return {"off": [{"skill": k, **v} for k, v in sorted(off.items())],
            "attempts": dict(attempts),
            "team": [_team_row(t) for t in await request.app.state.store.team_skills()]}


def _team_row(t: dict) -> dict:
    latest, live = t["latest"], t["live"]
    skill_md = next((f["content"] for f in latest["files"] if f["path"].lower() == "skill.md"), "")
    return {"name": t["name"], "description": latest.get("description", ""),
            "latest": {k: latest.get(k) for k in ("version", "by", "ts", "check")}
            | {"files": [f["path"] for f in latest["files"]]},
            "live": {k: live.get(k) for k in ("version", "by", "ts")} if live else None,
            "skill_md": skill_md[:4000]}


def _by(principal: object) -> str:
    if principal is None:
        return ""
    return str(getattr(principal, "email", "") or getattr(principal, "key_id", "") or "")


@router.put("/skills/{name}")
async def set_skill(name: str, body: dict, request: Request, principal: PrincipalDep,
                    store: StoreDep) -> dict:
    if not _NAME.match(name):
        raise HTTPException(400, "a skill name is a plain id (letters, digits, - _ . :)")
    if isinstance(body.get("team"), bool):
        return await _set_team(name, body["team"], request, principal, store)
    if not isinstance(body.get("off"), bool):
        raise HTTPException(400, 'body is {"off": true|false} or {"team": true|false}')
    off = await _off(request)
    if body["off"]:
        off[name] = {"ts": datetime.now(UTC).isoformat(), "by": _by(principal)}
        shared = await store.team_skill(name)
        if shared and shared["live"]:
            # off for the team is off the team: the sidecars remove their copy
            await store.put_team_skill(name, shared["latest"], None)
    else:
        off.pop(name, None)
    await request.app.state.store.set_setting(SKILLS_OFF, off)
    return {"skills_off": sorted(off)}


async def _set_team(name: str, on: bool, request: Request, principal: object,
                    store: StoreDep) -> dict:
    shared = await store.team_skill(name)
    if shared is None:
        raise HTTPException(404, f"no skill '{name}' was shared — `axor-wrap skill share "
                                 "<folder>` uploads one")
    if on and name in await _off(request):
        raise HTTPException(409, f"'{name}' is turned off for the team — turn it back on "
                                 "before sharing it")
    live = None
    if on:
        latest = shared["latest"]
        live = {"version": latest["version"], "files": latest["files"],
                "by": _by(principal), "ts": datetime.now(UTC).isoformat()}
    await store.put_team_skill(name, shared["latest"], live)
    return {"name": name, "team": on, "version": live["version"] if live else None}


# ── skills shared for the team ───────────────────────────────────────────────

_FOLDER_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")
MAX_FILES = 64
MAX_BYTES = 512_000


def _bundle_files(body: dict) -> list[dict]:
    files = body.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise HTTPException(400, f"files is a list of 1..{MAX_FILES} {{path, content}}")
    out, total = [], 0
    for f in files:
        path = str(f.get("path", "")).replace("\\", "/") if isinstance(f, dict) else ""
        content = f.get("content") if isinstance(f, dict) else None
        parts = path.split("/")
        if (not path or path.startswith("/") or not isinstance(content, str)
                or any(p in ("", ".", "..") or p.startswith(".") for p in parts)):
            raise HTTPException(400, f"a file's path stays inside the skill's folder: {path!r}")
        total += len(content)
        out.append({"path": path, "content": content})
    if total > MAX_BYTES:
        raise HTTPException(413, f"a shared skill is at most {MAX_BYTES} bytes of text")
    if not any(f["path"].lower() == "skill.md" for f in out):
        raise HTTPException(400, "a skill has its SKILL.md at the top of its folder")
    return sorted(out, key=lambda f: f["path"])


def _static_check(files: list[dict]) -> dict | None:
    try:
        from axor_wrap.agents.skills import check
    except ImportError:
        return None
    report = check([(f["path"], f["content"]) for f in files]).as_dict()
    return {"summary": report["summary"], "description": report.get("description", ""),
            "findings": report["findings"][:30]}


@router.post("/skill-bundles", status_code=201)
async def share_skill(body: dict, store: StoreDep, principal: PrincipalDep) -> dict:
    name = str(body.get("name", ""))
    if not _FOLDER_NAME.match(name):
        raise HTTPException(400, "a skill's name is its folder name (letters, digits, - _ .)")
    files = _bundle_files(body)
    version = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()[:12]
    check = _static_check(files)
    latest = {"version": version, "files": files, "by": _by(principal),
              "ts": datetime.now(UTC).isoformat(), "check": check,
              "description": (check or {}).get("description", "")}
    shared = await store.team_skill(name)
    live = shared["live"] if shared else None
    await store.put_team_skill(name, latest, live)
    return {"name": name, "version": version, "live_version": live["version"] if live else None,
            "check": check["summary"] if check else None}


@router.get("/skill-bundles/{name}")
async def team_bundle(name: str, store: StoreDep) -> dict:
    """The version on for the team — what a sidecar installs."""
    shared = await store.team_skill(name)
    if shared is None or not shared["live"]:
        raise HTTPException(404, f"'{name}' is not on for the team")
    return {"name": name, "version": shared["live"]["version"], "files": shared["live"]["files"]}
