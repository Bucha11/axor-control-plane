"""Agent tree runs: a tree of coding agents (axor-wrap `tree run`) as one view.

The tree run posts a snapshot of itself as it goes — stages, each node's agent
and model and the plane node id each run of it is, and the context items that
flowed between them, marked pushed, withheld (untrusted) or cut. Cutting an
item is NOT done here: it is the ordinary signed command door on the tree's own
node (`pending_excision`), which the run takes before its next stage.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/v1/trees", tags=["trees"])

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_STATUSES = ("running", "done", "failed")
MAX_DOC_BYTES = 1_000_000


@router.post("/{tree_run}", status_code=204)
async def put_tree(tree_run: str, body: dict, request: Request) -> None:
    if not _ID.match(tree_run):
        raise HTTPException(400, "tree_run must be a plain id")
    status = body.get("status", "running")
    if status not in _STATUSES:
        raise HTTPException(400, f"status must be one of {list(_STATUSES)}")
    if not isinstance(body.get("nodes", {}), dict) or not isinstance(body.get("items", []), list):
        raise HTTPException(400, "nodes is an object, items a list")
    doc = {k: body[k] for k in ("task", "stages", "nodes", "items", "flow", "excised")
           if k in body}
    if len(json.dumps(doc)) > MAX_DOC_BYTES:
        raise HTTPException(413, f"a tree snapshot is at most {MAX_DOC_BYTES} bytes")
    await request.app.state.store.put_tree_run(
        tree_run, status, doc, datetime.now(UTC).isoformat())


@router.get("")
async def list_trees(request: Request, limit: int = 50) -> dict:
    return {"trees": await request.app.state.store.list_tree_runs(max(1, min(limit, 200)))}


@router.get("/{tree_run}")
async def get_tree(tree_run: str, request: Request) -> dict:
    found = await request.app.state.store.get_tree_run(tree_run)
    if found is None:
        raise HTTPException(404, f"no tree run {tree_run!r}")
    return found
