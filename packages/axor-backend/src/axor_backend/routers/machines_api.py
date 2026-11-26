"""Machines that run work for their owner, and the work the owner queues.

A machine is a developer's `axor-wrap worker`, started by hand in a repository.
It reports with the developer's personal key, and the key's minter is the
machine's owner — decided here, never taken from what the machine says.

The owner queues work for their own machine from the UI — a skill eval today —
and only that owner's machine is offered it. Running one starts a coding agent
with permissions bypassed and a "done when" shell command the owner wrote: a
plane that handed it to anyone's machine would run code on every developer's
laptop. So a job names its owner, a machine names its owner, and the two must
be the same person on every route: queueing, listing, claiming.

    machine (ingest)   POST /v1/machines/heartbeat
                       GET  /v1/machines/{machine}/jobs
                       POST /v1/machines/{machine}/jobs/{job}/claim | progress | complete
    UI (operate)       GET  /v1/machines              (read)
                       POST /v1/machine-jobs
                       GET  /v1/machine-jobs[/{job}]   (read)
                       POST /v1/machine-jobs/{job}/cancel

A running job holds a lease the machine extends; a lapsed lease is `lost`,
never re-run on its own — the runs cost the owner's tokens.
"""
from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["machines"])

# a machine is "on line" while its heartbeats keep coming
ONLINE_SECONDS = 60
# a running job's lease; the worker extends it every minute, mid-run included
LEASE_SECONDS = 300
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
AGENTS = ("claude-code", "codex")
MAX_TRIALS = 50
MAX_TASKS = 20
MAX_SKILLS = 10
MAX_TEXT = 4000


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(t: datetime) -> str:
    return t.isoformat()


async def _owner(request: Request) -> str:
    """Whose this caller is: a signed-in person's user id, a personal key's
    minter, or "" for the operator (master token) and an open plane."""
    p = getattr(request.state, "principal", None)
    if p is None or p.kind == "master":
        return ""
    if p.kind == "user":
        return p.user_id or ""
    key = await request.app.state.store.get_api_key(p.key_id)
    return str((key or {}).get("created_by") or "")


def _plain_id(value: Any, what: str) -> str:  # noqa: ANN401 - request JSON
    if not isinstance(value, str) or not _ID.match(value):
        raise HTTPException(400, f"{what} must be a plain id (letters, digits, . _ : -)")
    return value


def _text(value: Any, what: str, *, required: bool = True) -> str:  # noqa: ANN401
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise HTTPException(400, f"{what} must be a non-empty string")
    if len(value) > MAX_TEXT:
        raise HTTPException(400, f"{what} is at most {MAX_TEXT} characters")
    return value


def _skill_eval_spec(spec: Any) -> dict[str, Any]:  # noqa: ANN401
    """What axor-eval's skill eval takes (axor_eval.skills.spec), checked, and
    pinned to the repository the worker runs in: `repo` is always "."."""
    if not isinstance(spec, dict):
        raise HTTPException(400, "spec must be an object")
    skills = spec.get("skills")
    if not isinstance(skills, list) or not 1 <= len(skills) <= MAX_SKILLS:
        raise HTTPException(400, f"skills must list 1 to {MAX_SKILLS} skill folders")
    tasks = spec.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= MAX_TASKS:
        raise HTTPException(400, f"tasks must list 1 to {MAX_TASKS} tasks")
    trials = spec.get("trials", 3)
    if not isinstance(trials, int) or isinstance(trials, bool) or not 1 <= trials <= MAX_TRIALS:
        raise HTTPException(400, f"trials must be 1 to {MAX_TRIALS}")
    agent = spec.get("agent", "claude-code")
    if agent not in AGENTS:
        raise HTTPException(400, f"agent must be one of {', '.join(AGENTS)}")
    out: dict[str, Any] = {
        "skills": [_text(s, "a skill") for s in skills],
        "repo": ".",
        "agent": agent,
        "trials": trials,
        "tasks": [
            {"name": _text(t.get("name") or f"task-{i + 1}", "a task's name"),
             "prompt": _text(t.get("prompt"), "a task's prompt"),
             "check": _text(t.get("check"), "a task's check")}
            for i, t in enumerate(tasks) if isinstance(t, dict)
        ],
    }
    if len(out["tasks"]) != len(tasks):
        raise HTTPException(400, "every task must be an object with a prompt and a check")
    if spec.get("model"):
        out["model"] = _text(spec["model"], "model")
    return out


def _view(job: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in job.items() if k != "owner"}


# ── the machine's side ──────────────────────────────────────────────────────

@router.post("/v1/machines/heartbeat")
async def heartbeat(body: dict, request: Request) -> dict:
    store = request.app.state.store
    machine_id = _plain_id(body.get("machine_id"), "machine_id")
    owner = await _owner(request)
    held = await store.get_machine(machine_id)
    if held is not None and held["owner"] != owner:
        # A machine id is one person's: another's key must not take it over
        # and, with it, the jobs queued for it.
        raise HTTPException(409, "this machine id is registered to someone else")
    agents = [a for a in body.get("agents") or [] if a in AGENTS]
    p = getattr(request.state, "principal", None)
    now = _now()
    await store.upsert_machine(
        machine_id, owner=owner, key_id=getattr(p, "key_id", "") or "",
        host=str(body.get("host") or "")[:120], repo_label=str(body.get("repo") or "")[:200],
        agents=agents, ts=_iso(now))
    await store.expire_machine_jobs(_iso(now))
    return {"machine_id": machine_id, "online_seconds": ONLINE_SECONDS,
            "lease_seconds": LEASE_SECONDS}


async def _own_machine(request: Request, machine_id: str) -> tuple[dict[str, Any], str]:
    owner = await _owner(request)
    machine = await request.app.state.store.get_machine(machine_id)
    if machine is None or machine["owner"] != owner:
        raise HTTPException(404, "no such machine for this key — send a heartbeat first")
    return machine, owner


@router.get("/v1/machines/{machine_id}/jobs")
async def machine_jobs(machine_id: str, request: Request) -> dict:
    _, owner = await _own_machine(request, machine_id)
    jobs = await request.app.state.store.list_machine_jobs(
        machine_id=machine_id, owner=owner, state="queued")
    return {"jobs": [_view(j) for j in reversed(jobs)]}   # oldest first


@router.post("/v1/machines/{machine_id}/jobs/{job_id}/claim")
async def claim(machine_id: str, job_id: str, request: Request) -> dict:
    _, owner = await _own_machine(request, machine_id)
    store = request.app.state.store
    now = _now()
    ok = await store.claim_machine_job(
        job_id, machine_id=machine_id, owner=owner,
        lease_until=_iso(now + timedelta(seconds=LEASE_SECONDS)), ts=_iso(now))
    if not ok:
        raise HTTPException(409, "this job is not waiting for this machine")
    return _view(await store.get_machine_job(job_id))


@router.post("/v1/machines/{machine_id}/jobs/{job_id}/progress")
async def progress(machine_id: str, job_id: str, body: dict, request: Request) -> dict:
    await _own_machine(request, machine_id)
    store = request.app.state.store
    now = _now()
    report = body.get("progress") if isinstance(body.get("progress"), dict) else {}
    ok = await store.report_machine_job(
        job_id, machine_id=machine_id,
        progress={k: report[k] for k in ("done", "total", "solved", "current") if k in report},
        lease_until=_iso(now + timedelta(seconds=LEASE_SECONDS)), ts=_iso(now))
    if not ok:
        raise HTTPException(409, "this job is not running on this machine")
    job = await store.get_machine_job(job_id)
    return {"cancel_requested": bool(job and job["cancel_requested"])}


@router.post("/v1/machines/{machine_id}/jobs/{job_id}/complete")
async def complete(machine_id: str, job_id: str, body: dict, request: Request) -> dict:
    await _own_machine(request, machine_id)
    state = body.get("state")
    if state not in ("done", "failed", "cancelled"):
        raise HTTPException(400, "state must be done, failed or cancelled")
    eval_id = body.get("eval_id")
    if eval_id is not None:
        eval_id = _plain_id(eval_id, "eval_id")
    if state == "done" and eval_id is None:
        raise HTTPException(400, "a done skill eval names the report it posted (eval_id)")
    ok = await request.app.state.store.finish_machine_job(
        job_id, machine_id=machine_id, state=state, eval_id=eval_id,
        error=str(body.get("error") or ""), ts=_iso(_now()))
    if not ok:
        raise HTTPException(409, "this job is not running on this machine")
    return {"job_id": job_id, "state": state}


# ── the UI's side ───────────────────────────────────────────────────────────

@router.get("/v1/machines")
async def list_machines(request: Request) -> dict:
    owner = await _owner(request)
    cutoff = _iso(_now() - timedelta(seconds=ONLINE_SECONDS))
    machines = await request.app.state.store.list_machines()
    return {"machines": [
        {**{k: v for k, v in m.items() if k not in ("owner", "key_id")},
         "online": m["last_seen"] >= cutoff, "yours": m["owner"] == owner}
        for m in machines
    ]}


@router.post("/v1/machine-jobs", status_code=201)
async def queue_job(body: dict, request: Request) -> dict:
    store = request.app.state.store
    owner = await _owner(request)
    machine_id = _plain_id(body.get("machine_id"), "machine_id")
    machine = await store.get_machine(machine_id)
    if machine is None:
        raise HTTPException(404, "no such machine")
    if machine["owner"] != owner:
        raise HTTPException(403, "a job runs only on your own machine: it starts a coding "
                                 "agent and a shell command there")
    if body.get("kind", "skill_eval") != "skill_eval":
        raise HTTPException(400, "kind must be skill_eval")
    spec = _skill_eval_spec(body.get("spec"))
    p = getattr(request.state, "principal", None)
    job_id = f"job-{uuid.uuid4().hex[:12]}"
    await store.create_machine_job(
        job_id, machine_id=machine_id, owner=owner,
        created_by=getattr(p, "email", None) or "", kind="skill_eval", spec=spec,
        ts=_iso(_now()))
    return _view(await store.get_machine_job(job_id))


@router.get("/v1/machine-jobs")
async def list_jobs(request: Request, limit: int = 50) -> dict:
    store = request.app.state.store
    await store.expire_machine_jobs(_iso(_now()))
    jobs = await store.list_machine_jobs(limit=max(1, min(limit, 200)))
    return {"jobs": [_view(j) for j in jobs]}


@router.get("/v1/machine-jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> dict:
    store = request.app.state.store
    await store.expire_machine_jobs(_iso(_now()))
    job = await store.get_machine_job(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return _view(job)


@router.post("/v1/machine-jobs/{job_id}/cancel")
async def cancel(job_id: str, request: Request) -> dict:
    store = request.app.state.store
    job = await store.get_machine_job(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    if job["owner"] != await _owner(request):
        raise HTTPException(403, "only the job's owner cancels it")
    state = await store.cancel_machine_job(job_id, _iso(_now()))
    return {"job_id": job_id, "state": state, "stops": "now" if state == "cancelled"
            else "after the run in progress"}
