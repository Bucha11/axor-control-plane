"""Axor Lab cross-links: CP → Lab incident export, Lab → CP policy deploy.

The two halves of one loop. A production incident leaves here as a reproducible
package the Lab can import; what the Lab proves out comes back as a cp-deploy
package whose pins join the regression corpus. Only finalized, evidence-backed
packages are accepted — a rejection lists every reason rather than the first.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from axor_backend.clock import now
from axor_backend.deps import StoreDep, SubgraphCacheDep
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1", tags=["lab"])


@router.get("/runs/{run_id}/lab-package")
async def run_lab_package(run_id: str, store: StoreDep) -> dict:
    """The run as an axor-lab-incident/v1 package (trace + scenario + manifests
    + recorded condition) — the input of `axor-lab import-incident`. 422 with
    the full reason list when the run is not convertible (proxy-depth events,
    unreproducible verdicts, no vector)."""
    from axor_backend.lab_export import LabExportError, build_incident_package

    runs = {r["run_id"]: r for r in await store.list_runs()}
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(404, f"no such run {run_id}")
    events = [json.loads(line) for line in await store.run_events(run_id)]
    try:
        return build_incident_package(events, run)
    except LabExportError as exc:
        raise HTTPException(
            422,
            {"error": "run is not convertible to a Lab incident package",
             "reasons": list(exc.reasons)},
        ) from exc


@router.post("/lab/deploy")
async def lab_deploy(body: dict, store: StoreDep, cache: SubgraphCacheDep) -> dict:
    """Accept a cp-deploy.json produced by `axor-lab export-cp`: validate
    (finalized evidence-backed packages only), store the package record, and
    fold its regression pins into the corpus with source lab:{id}."""
    from axor_backend.lab_import import (
        deploy_plans,
        package_id_of,
        validate_cp_deploy,
    )

    problems = validate_cp_deploy(body)
    if problems:
        raise HTTPException(
            422, {"error": "cp-deploy package rejected", "reasons": problems},
        )
    package_id = package_id_of(body)
    plans = deploy_plans(body, package_id)
    stored_new = await store.add_lab_deploy(package_id, body, len(plans), now())
    for plan in plans:  # idempotent per run_id — a re-upload re-asserts them
        await store.pin(plan.run_id, plan.side, plan.label)
        # A pin whose carried trace was recorded under the real axor-core kernel
        # and reproduces here is stored as replayable corpus events — the
        # regression report folds them instead of skipping the pin.
        if plan.replayable:
            await store.add_lab_trace_events(plan.run_id, plan.event_lines)
            cache.drop_run(current_org_id(), plan.run_id)
    return {
        "package_id": package_id,
        "pins_created": len(plans),
        # How many Lab pins are now REPLAYABLE corpus traces (real-kernel,
        # build-matched, verdict reproduces) vs left skipped with a reason.
        "pins_replayable": sum(1 for p in plans if p.replayable),
        "pins_skipped": [
            {"run_id": p.run_id, "trace_id": p.trace_id, "reason": p.reason}
            for p in plans if not p.replayable
        ],
        "policy_stored": True,
        "already_deployed": not stored_new,
    }


@router.get("/lab/deploys")
async def lab_deploys_list(store: StoreDep) -> list[dict]:
    return await store.list_lab_deploys()
