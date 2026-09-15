"""The regression corpus: pins, the config CI that runs over them, and its
schedule.

The free/paid line runs straight through this file and is worth stating plainly:
pinning is free, running the corpus manually is free **forever** (monetization
Line 1 — a safety check never asks for a license). What EE buys is the *org*
layer around it: the history of past runs, and having it fire on a schedule
instead of when somebody remembers.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from axor_backend.corpus import record_corpus_run, regression_report
from axor_backend.deps import StateDep, StoreDep
from axor_backend.licensing import active_license, require_ee
from axor_backend.replay_api import kernel_config_from_json
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1", tags=["corpus"])


@router.post("/pins/{run_id}")
async def pin_run(run_id: str, body: dict, store: StoreDep) -> dict:
    side = body.get("side")
    if side not in ("must_block", "must_pass"):
        raise HTTPException(400, "side must be must_block|must_pass")
    await store.pin(run_id, side, body.get("label", ""))
    return {"pinned": run_id, "side": side}


@router.get("/pins")
async def list_pins(store: StoreDep) -> dict:
    """The regression corpus as a flat list + side counts. This is the
    North-star surface: how many caught EvidenceCases the operator committed to
    permanent checks (must_block auto-pins on evidence, must_pass by hand). A
    growing corpus means Axor's findings were trusted enough to guard against
    forever — the signal that the loop closed."""
    pins = await store.pinned()
    return {
        "pins": pins,
        "must_block": sum(1 for p in pins if p["side"] == "must_block"),
        "must_pass": sum(1 for p in pins if p["side"] == "must_pass"),
        "total": len(pins),
    }


@router.post("/regression")
async def regression(body: dict, state: StateDep, store: StoreDep) -> dict:
    """Config CI over the pinned corpus (decision 11): a corpus needs both
    sides, or a config that blocks everything passes. Manual runs are free
    forever (Line 1); every run leaves a history row and a failing corpus fires
    the regression_failed trigger."""
    report = await regression_report(store, body.get("config", {}))
    await record_corpus_run(state, report, "manual")
    return report


@router.get("/regression/history")
async def regression_history(
    state: StateDep, store: StoreDep, limit: int = 50
) -> list[dict]:
    """Corpus-run history — the org surface (EE): "when did this config last
    regress"."""
    require_ee(state, current_org_id(), "regression history")
    return await store.list_regression_reports(limit)


@router.get("/regression/schedule")
async def get_regression_schedule(state: StateDep, store: StoreDep) -> dict:
    """Free to read (the UI shows the locked state); writing needs EE."""
    sched = await store.get_setting("regression_schedule")
    return {
        "enabled": bool(sched and sched.get("enabled")),
        "interval_hours": (sched or {}).get("interval_hours"),
        "last_run_ts": (sched or {}).get("last_run_ts"),
        "ee_active": active_license(state, current_org_id()) is not None,
    }


@router.put("/regression/schedule")
async def put_regression_schedule(
    body: dict, state: StateDep, store: StoreDep
) -> dict:
    """Scheduled corpus CI (EE): store {enabled, interval_hours, config}; the
    sweep loop fires it when due and regression_failed gets loud."""
    require_ee(state, current_org_id(), "scheduled corpus CI")
    enabled = bool(body.get("enabled"))
    try:
        interval = float(body.get("interval_hours", 24))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "interval_hours must be a number") from exc
    if enabled and interval < 1:
        raise HTTPException(400, "interval_hours must be >= 1")
    config = body.get("config", {})
    if not isinstance(config, dict):
        raise HTTPException(400, "config must be an object")
    # Setting a schedule validates the config now, not at 3am.
    kernel_config_from_json(config)
    # One atomic read-modify-write: `last_run_ts` belongs to the sweep, which
    # may be stamping it right now, and the rest belongs to the operator. Read
    # then write dropped whichever of the two lost the race — either the new
    # schedule, or the stamp that stops the sweep running again immediately.
    def write(stored: Any) -> dict:  # noqa: ANN401
        return {
            "enabled": enabled,
            "interval_hours": interval,
            "config": config,
            "last_run_ts": (stored or {}).get("last_run_ts"),
        }

    await store.mutate_setting("regression_schedule", write)
    return {"enabled": enabled, "interval_hours": interval}
