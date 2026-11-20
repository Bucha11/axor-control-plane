"""Coding-agent setup health (axor-eval setup_health): checks posted by
`python -m axor_eval.setup_health run … --plane`, read back as the latest and
the history. The latest is also the next check's baseline."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, HTTPException

from axor_backend.clock import now
from axor_backend.deps import StoreDep

router = APIRouter(prefix="/v1/setup-health", tags=["setup-health"])

MAX_REPORT_BYTES = 2_000_000
_KEEP_RUN_TEXT = 300


def _validated(body: dict) -> dict:
    if body.get("kind") != "setup-health" or not isinstance(body.get("families_raw"), dict):
        raise HTTPException(400, "a setup health report is what axor_eval.setup_health writes "
                                 "(kind: setup-health, families_raw)")
    doc = dict(body)
    doc["runs"] = [{**r, "evidence": str(r.get("evidence", ""))[:_KEEP_RUN_TEXT]}
                   for r in body.get("runs") or [] if isinstance(r, dict)]
    if len(json.dumps(doc)) > MAX_REPORT_BYTES:
        raise HTTPException(413, f"a setup health report is at most {MAX_REPORT_BYTES} bytes")
    return doc


@router.post("", status_code=201)
async def post_check(body: dict, store: StoreDep) -> dict:
    check_id = f"sh-{uuid.uuid4().hex[:12]}"
    await store.put_setup_health(check_id, _validated(body), now())
    return {"check_id": check_id}


@router.get("")
async def list_checks(store: StoreDep, limit: int = 20) -> dict:
    return {"checks": await store.list_setup_health(max(1, min(limit, 100)))}


@router.get("/latest")
async def latest_check(store: StoreDep) -> dict:
    checks = await store.list_setup_health(1)
    return {"check": checks[0] if checks else None}
