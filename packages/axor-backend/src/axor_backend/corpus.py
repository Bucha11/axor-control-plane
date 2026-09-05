"""The pinned regression corpus — config CI for governance (decision 11).

A corpus needs BOTH sides or the exercise is theatre: a config that blocks
everything passes a must_block-only corpus. ``must_block`` auto-pins when a run
carries an EvidenceCase; ``must_pass`` is pinned by hand.

One report function, two callers — the manual route and the EE scheduler — so a
scheduled run and an operator's run are the same computation, and history rows
from either are comparable.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from axor_backend.clock import now
from axor_backend.replay_api import kernel_config_from_json, regression_row
from axor_backend.storage import Store
from axor_backend.traces import events_for


async def regression_report(store: Store, config_json: dict) -> dict:
    """Run the pinned corpus under a config."""
    config = kernel_config_from_json(config_json)
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for pin in await store.pinned():
        # A pin whose run has no replayable kernel trace (deleted events,
        # telemetry-only) must not 4xx the whole report — skip it and say so.
        try:
            events = await events_for(store, pin["run_id"])
        except HTTPException:
            skipped.append(pin["run_id"])
            continue
        rows.append(
            regression_row(pin["run_id"], pin["side"], pin["label"], events, config)
        )
    regressed = sum(1 for r in rows if r["result"] == "regressed")
    escaped = sum(1 for r in rows if r["result"] == "escaped")
    return {
        "rows": rows,
        "regressed": regressed,
        "escaped": escaped,
        "skipped": skipped,
        "safe_to_ship": regressed == 0 and escaped == 0,
    }


async def record_corpus_run(state: Any, report: dict, source: str) -> None:  # noqa: ANN401
    """Every corpus run leaves history; a failing one gets loud (spec §16)."""
    await state.store.add_regression_report(report, source, now())
    if report["regressed"] or report["escaped"]:
        await state.notifier.emit(
            "regression_failed",
            "corpus",
            {
                "source": source,
                "regressed": report["regressed"],
                "escaped": report["escaped"],
                "total": len(report["rows"]),
            },
        )
