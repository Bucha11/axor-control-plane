"""Startup, shutdown, and the loops that run between them.

The backend keeps four derived things in process memory — the provenance graph,
the share registry, the notifier's subscriptions, and the verified licenses —
and all four are rebuilt here from the database at boot. The event log is the
system of record; memory is a working copy. That is also why the deployment is
one process: a second worker would hold a second, independent copy of all four,
plus its own event bus.

Every background loop faces the same hazard, and each one names it: a task has
no request, so the ambient tenant is the public one. Sweeping under it would
silently exempt every other organization. Each loop iterates
:meth:`Store.list_orgs` (or the narrower ``orgs_with_setting``) and sets the
tenant per iteration.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI

from axor_backend.corpus import record_corpus_run, regression_report
from axor_backend.graph import rehydrate_all_graphs
from axor_backend.licensing import active_license, load_licenses
from axor_backend.monitor import running_stale_monitor
from axor_backend.storage import init_db
from axor_backend.tenancy import PUBLIC_ORG, set_current_org

log = logging.getLogger("axor.backend")

RETENTION_SWEEP_SECONDS = 6 * 3600


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Boot the deployment, run it, and stop its tasks cleanly."""
    warn_about_open_posture(app.state.config)
    await init_db(app.state.store.engine)
    # Retention runs at boot BEFORE the projections are rebuilt: pruning deletes
    # runs, and a graph rehydrated first would carry edges for events that no
    # longer exist.
    await prune_once(app.state)
    await rehydrate(app.state)
    tasks = [asyncio.create_task(regression_schedule_loop(app.state))]
    if app.state.config.retention_days:
        tasks.append(asyncio.create_task(retention_loop(app.state)))
    try:
        # The node_stale trigger is edge-detected by a background sweep (spec
        # §16): a silent node emits nothing, so its absence is what we watch.
        async with running_stale_monitor(app):
            yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


def warn_about_open_posture(config: Any) -> None:  # noqa: ANN401 - AppConfig
    """Open dev posture must be loud (SECURITY.md).

    All three of these default to off, and silence reads as "the wall is up"
    when it is not. No token means every endpoint is public; unsigned commands
    mean no operator integrity; a vault without its own token falls back to the
    scope ladder alone instead of its separated credential.
    """
    if not config.auth_enabled:
        log.warning(
            "AUTH IS OFF (no AXOR_API_TOKEN) — every endpoint is open. "
            "Fine for localhost, not for a deployment; see SECURITY.md."
        )
    if config.allow_unsigned and not config.operator_keys:
        log.warning(
            "UNSIGNED PLANE COMMANDS ACCEPTED (AXOR_ALLOW_UNSIGNED=1, no "
            "operator keys) — set AXOR_OPERATOR_KEYS for any real deployment."
        )
    for subsystem, env in (("creds", "AXOR_VAULT_CREDS_TOKEN"),
                           ("signing", "AXOR_VAULT_SIGNING_TOKEN")):
        if getattr(config, f"vault_{subsystem}_token") is None:
            log.warning(
                "VAULT '%s' HAS NO SEPARATE TOKEN (%s unset) — the wall "
                "between dispensing credentials and requesting signatures is "
                "down; admin scope is the only check. Set it for any real "
                "deployment (spec v2 Ch.5 §3).",
                subsystem, env,
            )


async def rehydrate(state: Any) -> None:  # noqa: ANN401 - app.state is dynamic
    """Rebuild every in-memory projection from the database."""
    # The taint graph is a derived index over the persisted event log — rebuild
    # it so it survives restarts (and a fresh instance catches up) without a
    # graph database being part of the deployment.
    await rehydrate_all_graphs(state.store, state.graphs)
    # Share links and notification subscriptions are primary data: rebuild their
    # in-memory holders so a restart keeps permalinks live and keeps
    # notifications firing (see storage.share_links / _subs).
    for link in await state.store.list_share_links():
        state.shares.load(
            link["token"], link["run_id"], link["case_index"], link["revoked"],
            org=link["org_id"],
        )
    for sub in await state.store.all_subscriptions():
        state.notifier.subscribe(
            sub["url"], sub["triggers"], sub["debounce_seconds"],
            label=sub.get("label", ""),
            node_pattern=sub.get("node_pattern", "*"),
            org=sub["org_id"],
        )
    await load_licenses(state)


# ── retention (launch-readiness §1) ───────────────────────────────────────────

async def prune_once(state: Any) -> None:  # noqa: ANN401
    """Drop runs older than the configured window, in every tenant."""
    days = state.config.retention_days
    if days is None or days <= 0:  # unset = keep forever
        return
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    try:
        for org in await state.store.list_orgs():
            set_current_org(org)
            pruned = await state.store.prune_runs_older_than(cutoff)
            if pruned:
                log.info(
                    "retention: pruned %d runs older than %s (org %s)",
                    pruned, cutoff, org,
                )
    finally:
        set_current_org(PUBLIC_ORG)


async def retention_loop(state: Any) -> None:  # noqa: ANN401
    while True:
        await asyncio.sleep(RETENTION_SWEEP_SECONDS)
        with contextlib.suppress(Exception):
            await prune_once(state)


# ── EE scheduled corpus CI ────────────────────────────────────────────────────

async def regression_schedule_loop(state: Any) -> None:  # noqa: ANN401
    """Fire each tenant's corpus when its operator-set interval is due.

    The license is checked at fire time, so an expired one pauses the schedule
    (EE read-only) without touching the stored setting.
    """
    sweep = state.config.schedule_sweep_seconds
    while True:
        await asyncio.sleep(sweep)
        try:
            for org in await state.store.orgs_with_setting("regression_schedule"):
                set_current_org(org)
                await run_due_schedule(state, org)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive a bad cycle
            log.exception("scheduled corpus sweep failed")
        finally:
            set_current_org(PUBLIC_ORG)


async def run_due_schedule(state: Any, org: str) -> None:  # noqa: ANN401
    """One tenant's scheduled run. A failure here must not stop the sweep
    reaching the other organizations."""
    try:
        sched = await state.store.get_setting("regression_schedule")
        if not sched or not sched.get("enabled"):
            return
        if active_license(state, org) is None:
            return
        last = sched.get("last_run_ts")
        interval = timedelta(hours=float(sched.get("interval_hours", 24)))
        now_ts = datetime.now(UTC)
        if last is not None and now_ts - datetime.fromisoformat(last) < interval:
            return
        report = await regression_report(state.store, sched.get("config", {}))
        await record_corpus_run(state, report, "scheduled")
        sched["last_run_ts"] = now_ts.isoformat()
        await state.store.set_setting("regression_schedule", sched)
        log.info(
            "scheduled corpus run (org %s): %d rows, regressed=%d escaped=%d",
            org, len(report["rows"]), report["regressed"], report["escaped"],
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - one tenant's bad cycle is not the fleet's
        log.exception("scheduled corpus run failed for org %s", org)
