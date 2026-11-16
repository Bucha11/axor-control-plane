"""Skill evals: which coding-agent skills help, and what they cost.

`python -m axor_eval.skills run SPEC --plane` posts its report here (the same
JSON it writes with --out): per skill, what it does alone and on top of the
others, whether the agent opened it, and its token cost; per arm, solved k/n,
tokens and cost. The plane stores it as written and serves it back — it
re-judges nothing. Each trial's agent answer and check output are cut short:
the report is the evidence, not a log store.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/v1/skill-evals", tags=["skill-evals"])

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
MAX_REPORT_BYTES = 2_000_000
_CUT = 600


def _validated(body: dict) -> dict:
    skills = body.get("skills")
    if not isinstance(skills, list) or not skills or not all(isinstance(s, str) for s in skills):
        raise HTTPException(400, "skills must be a non-empty list of names")
    for key in ("per_skill", "per_task", "trials", "arms"):
        if not isinstance(body.get(key, []), list):
            raise HTTPException(400, f"{key} must be a list")
    if not isinstance(body.get("overall", {}), dict):
        raise HTTPException(400, "overall must be an object")
    doc = {k: body[k] for k in ("agent", "model", "skills", "arms", "per_skill", "overall",
                                "per_task", "trials") if k in body}
    doc["example"] = False   # only the plane's own example says it is one
    doc["trials"] = [
        {**t, "answer": str(t.get("answer", ""))[:_CUT],
         "check_output": str(t.get("check_output", ""))[-_CUT:]}
        for t in doc.get("trials", []) if isinstance(t, dict)
    ]
    if len(json.dumps(doc)) > MAX_REPORT_BYTES:
        raise HTTPException(413, f"a skill eval report is at most {MAX_REPORT_BYTES} bytes")
    return doc


@router.post("", status_code=201)
async def post_eval(body: dict, request: Request) -> dict:
    eval_id = body.get("eval_id") or f"se-{uuid.uuid4().hex[:10]}"
    if not isinstance(eval_id, str) or not _ID.match(eval_id):
        raise HTTPException(400, "eval_id must be a plain id")
    await request.app.state.store.put_skill_eval(
        eval_id, _validated(body), datetime.now(UTC).isoformat())
    return {"eval_id": eval_id}


@router.get("")
async def list_evals(request: Request, limit: int = 50) -> dict:
    return {"evals": await request.app.state.store.list_skill_evals(max(1, min(limit, 200)))}


@router.get("/{eval_id}")
async def get_eval(eval_id: str, request: Request) -> dict:
    found = await request.app.state.store.get_skill_eval(eval_id)
    if found is None:
        raise HTTPException(404, f"no skill eval {eval_id!r}")
    return found
