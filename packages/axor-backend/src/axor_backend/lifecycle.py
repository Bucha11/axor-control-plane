"""Startup, shutdown, and the loops that run between them.

The backend keeps three things in process memory — the share registry, the
notifier's subscriptions, and the verified licenses — and all three are rebuilt
here from the database at boot. The database is the system of record; memory is
a working copy. That is also why the deployment is one process: a second worker
would hold a second, independent copy of all three, plus its own event bus.

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
    # runs, and a share link rehydrated first would point at one that is gone.
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

    All of these default to off, and silence reads as "the wall is up" when it
    is not. No token means every endpoint is public; unsigned commands mean no
    operator integrity; a vault without its own token falls back to the scope
    ladder alone instead of its separated credential; and no licensed org means
    any vendor-signed license activates here, including one issued to somebody
    else.
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
    if config.vendor_pubkey and not config.org:
        log.warning(
            "NO LICENSED ORGANIZATION (AXOR_ORG unset) — a license names the "
            "organization it was issued to, and with nothing to compare it "
            "against ANY vendor-signed license activates here, including one "
            "issued to another customer. Set AXOR_ORG to the name on your "
            "license."
        )


async def rehydrate(state: Any) -> None:  # noqa: ANN401 - app.state is dynamic
    """Rebuild every in-memory projection from the database."""
    # Value provenance is NOT rebuilt here, and no longer exists as a projection
    # at all: it is derived from one run's events when a request asks for it
    # (see axor_backend.provenance). A projection keyed on value refs was a
    # projection keyed on names that repeat in every run.
    # Decision #13 says it in terms: "own crypto storage is a red flag in a
    # security product; the differentiator is scoped sink-side injection, not
    # storage." What ships is the lightweight dev backend that decision allows —
    # the settings KV, and not even encrypted at rest. Silence would read as "a
    # secret store is underneath"; it is not.
    creds = await state.store.get_setting("vault_creds/v1")
    plaintext = [k for k, e in (creds or {}).items() if not e.get("sealed_secret")]
    if plaintext:
        log.warning(
            "TOOL CREDENTIALS ARE IN THE DEV BACKEND (%d of %d enrolled held "
            "in plaintext) — the settings table, unencrypted. Fine for a test "
            "bench. Register a sealing key (POST /v1/vault/creds/sealing-key, "
            "`axor-proxy vault keygen`) and this deployment stores only what it "
            "cannot open (ui-spec §14.2); a real one also plugs a Vault/KMS-"
            "class store behind the same interface (decision #13).",
            len(plaintext), len(creds),
        )
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
        with contextlib.suppress(Exception):
            await license_sweep_once(state)


async def license_sweep_once(state: Any) -> None:  # noqa: ANN401
    """The entitlement housekeeping pass, per tenant.

    Three things that all used to be answered once and never again, or never at
    all: whether the fleet outgrew its licensed ceiling, whether the license is
    about to run out, and whether a newer one is waiting to be fetched.

    Renewal runs FIRST, so a license that renews in this pass is not also
    announced as expiring in it. None of the three ever refuses anything: a
    governed node is a safety surface, and safety never checks a license.
    """
    from axor_backend.licensing import (
        notify_expiring,
        renew_once,
        renewal_due,
        warn_over_ceiling,
    )

    try:
        for org in await state.store.list_orgs():
            set_current_org(org)
            if await renewal_due(state, org):
                await renew_once(state, org)
            with contextlib.suppress(Exception):
                await notify_expiring(state, org)
            with contextlib.suppress(Exception):
                await warn_over_ceiling(state, org)
    finally:
        set_current_org(PUBLIC_ORG)


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
        # Stamp the run time WITHOUT storing back the schedule read above: the
        # regression report between the two takes real time, and an operator who
        # changed the schedule meanwhile would have had it erased by this write.
        def stamp(stored: Any) -> dict:  # noqa: ANN401
            return {**(stored or {}), "last_run_ts": now_ts.isoformat()}

        await state.store.mutate_setting("regression_schedule", stamp)
        log.info(
            "scheduled corpus run (org %s): %d rows, regressed=%d escaped=%d",
            org, len(report["rows"]), report["regressed"], report["escaped"],
        )
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - one tenant's bad cycle is not the fleet's
        log.exception("scheduled corpus run failed for org %s", org)
