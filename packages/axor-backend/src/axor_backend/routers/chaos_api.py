"""Chaos tests (chaos.py): the list, arming one on live agents, and the
schedule that arms them on its own (Team, like scheduled corpus CI)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from axor_backend import chaos
from axor_backend.deps import StateDep, StoreDep
from axor_backend.licensing import active_license, require_ee
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1/chaos", tags=["chaos"])


@router.get("")
async def list_chaos(state: StateDep, store: StoreDep) -> dict:
    sched = await store.get_setting("chaos_schedule") or {}
    pending, said = await chaos.armed(store)
    return {
        "runs": await chaos.chaos_runs(store),
        "armed": pending,
        "answered": said,
        "modes": list(chaos.MODES),
        "schedule": {
            "enabled": bool(sched.get("enabled")),
            "interval_hours": sched.get("interval_hours"),
            "nodes": sched.get("nodes", []),
            "modes": sched.get("modes", list(chaos.MODES)),
            "last_run_ts": sched.get("last_run_ts"),
            "last_armed": sched.get("last_armed", []),
            "ee_active": active_license(state, current_org_id()) is not None,
        },
    }


@router.get("/tools/{node_id}")
async def chaos_tools(node_id: str, store: StoreDep) -> dict:
    return {"tools": await chaos.tools_of(store, node_id)}


def _modes(raw: object) -> list[str]:
    if raw is None:
        return list(chaos.MODES)
    if not isinstance(raw, list) or not raw or any(m not in chaos.MODES for m in raw):
        raise HTTPException(400, f"modes is a non-empty list of {', '.join(chaos.MODES)}")
    return [str(m) for m in raw]


@router.post("/arm")
async def arm_chaos(body: dict, state: StateDep, store: StoreDep) -> dict:
    """Break a tool on each named agent's next run: the tool named, or one it
    has used; the mode named, or one at random."""
    nodes = body.get("nodes")
    if not isinstance(nodes, list) or not nodes or not all(isinstance(n, str) for n in nodes):
        raise HTTPException(400, "nodes is a non-empty list of agent ids")
    tool = body.get("tool") or None
    mode = body.get("mode") or None
    if mode is not None and mode not in chaos.MODES:
        raise HTTPException(400, f"mode is one of {', '.join(chaos.MODES)}")
    picked, skipped = await chaos.plan(store, nodes, tool, mode)
    armed = [await chaos.arm(state, node, [fault], str(body.get("operator") or "op_ui"))
             for node, fault in picked]
    return {"armed": armed, "skipped": skipped}


@router.put("/schedule")
async def put_chaos_schedule(body: dict, state: StateDep, store: StoreDep) -> dict:
    """Arm a chaos test on these agents every N hours (Team): a tool each has
    used, broken one of these ways, at random."""
    require_ee(state, current_org_id(), "scheduled chaos tests")
    enabled = bool(body.get("enabled"))
    try:
        interval = float(body.get("interval_hours", 24))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "interval_hours must be a number") from exc
    if enabled and interval < 1:
        raise HTTPException(400, "interval_hours must be >= 1")
    nodes = body.get("nodes", [])
    if not isinstance(nodes, list) or not all(isinstance(n, str) for n in nodes):
        raise HTTPException(400, "nodes is a list of agent ids (empty: every agent)")
    modes = _modes(body.get("modes"))

    def write(stored: Any) -> dict:  # noqa: ANN401 - the sweep owns last_run_ts
        return {**(stored or {}), "enabled": enabled, "interval_hours": interval,
                "nodes": nodes, "modes": modes}

    await store.mutate_setting("chaos_schedule", write)
    return {"enabled": enabled, "interval_hours": interval, "nodes": nodes, "modes": modes}
