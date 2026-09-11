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
    """Run the pinned corpus under a config.

    Two ways a pin does not produce a row, and both withhold the verdict:
    `unanchored` (replayed, but its trace recorded no denial to re-check) and
    `skipped` (its trace could not be read at all). Each `skipped` entry carries
    the reason `traces.events_for` gave, because "1 pin skipped" without one
    leaves the operator with no next move.
    """
    config = kernel_config_from_json(config_json)
    rows: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for pin in await store.pinned():
        # A pin whose run has no replayable kernel trace (deleted events,
        # telemetry-only, a Lab package recorded under another kernel build)
        # must not 4xx the whole report — skip it and say so, with the reason.
        try:
            events = await events_for(store, pin["run_id"])
        except HTTPException as exc:
            skipped.append({"run_id": pin["run_id"], "side": pin["side"],
                            "label": pin["label"], "reason": str(exc.detail)})
            continue
        rows.append(
            regression_row(pin["run_id"], pin["side"], pin["label"], events, config)
        )
    regressed = sum(1 for r in rows if r["result"] == "regressed")
    escaped = sum(1 for r in rows if r["result"] == "escaped")
    # A must_block pin whose trace recorded no denial cannot be checked against
    # itself (replay_api.regression_row). Counted, and it withholds
    # safe_to_ship: the report promises "every attack still blocked", and a pin
    # nothing was verified against is not evidence for that sentence.
    unanchored = sum(1 for r in rows if r["result"] == "unanchored")
    return {
        "rows": rows,
        "regressed": regressed,
        "escaped": escaped,
        "unanchored": unanchored,
        "skipped": skipped,
        # `skipped` and an empty corpus withhold it for exactly the reason
        # `unanchored` does, three lines up. A pin whose trace could not be read
        # was verified against nothing; a corpus with no pins verified nothing
        # at all. Either way "every attack still blocked" is a sentence with no
        # evidence behind it, and it is the strongest sentence this product
        # says — it used to come out green with every pin unreadable, taking the
        # scheduled run's history row and its silence with it.
        "safe_to_ship": (
            bool(rows)
            and not skipped
            and regressed == 0
            and escaped == 0
            and unanchored == 0
        ),
    }


async def record_corpus_run(state: Any, report: dict, source: str) -> None:  # noqa: ANN401
    """Every corpus run leaves history; a failing one gets loud (spec §16)."""
    await state.store.add_regression_report(report, source, now())
    if not report["safe_to_ship"]:
        await state.notifier.emit(
            "regression_failed",
            "corpus",
            {
                "source": source,
                "regressed": report["regressed"],
                "escaped": report["escaped"],
                "unanchored": report["unanchored"],
                # Named, or a report failing only on unreadable pins would
                # notify with four zeroes and no reason for the alert.
                "skipped": len(report["skipped"]),
                "total": len(report["rows"]),
            },
        )
