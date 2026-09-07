"""EE entitlement state (monetization §4): verified once, persisted, honest.

A license entitles ONE organization, so the verified licenses live in a dict
keyed by org rather than a process-wide slot — otherwise whichever tenant pasted
last would decide everyone else's tier.

The rule that shapes every function here: **safety never checks a license**
(monetization Line 1). :func:`require_ee` is called only by org-layer surfaces —
scheduled corpus CI, run history, notification routing. A gate, a degradation
level, a denial, a manual corpus run: never.

What a license actually has to answer, and each of these was a separate hole:

* **Is it ours?** The signed payload names the organization it was issued to and
  nothing compared that name to anything, so one purchased file activated in any
  tenant of any deployment. :func:`binding_error` compares it — to
  ``AXOR_ORG`` when the operator pinned one, else to the identity tenant pasting
  it. A single-tenant install that pins neither is unbound, and boot says so.
* **Is it current?** Expiry was checked when a feature was USED and not when the
  license was stored, so ``/verify`` answered 200 ``activated: true`` for a
  license that expired in 2020 and ``/status`` then said ``active: false``.
* **Does it cover this?** The tier answers it, and only the tier. Private Lab
  and the Control Plane were separately licensed flags on top of a rung; they
  are one product on one ladder now, so a rung that entitles one entitles the
  other. ``modules`` is gone from the format rather than pinned to
  ``{true, true}`` — it was signed, reported, and read as though it decided
  something while every ``require_ee`` call omitted ``module=``.
* **How much of it?** ``allows_nodes`` existed and was called from nowhere, and
  ``over_ceiling`` was computed once, at paste time. Over-ceiling never blocks —
  a governed node is safety — but it must be visible, so it is recomputed on the
  housekeeping sweep and reported by ``/status``. It counts the PEAK of the
  current billing month, from `node_activity`; it used to count
  ``list_nodes()``, the union of desired and reported state, which has no time
  in it at all and therefore only ever grew — a customer who replaced one node
  was over their allowance forever, and no invoice could have been drawn from
  it.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException

from axor_backend.tenancy import PUBLIC_ORG, set_current_org

log = logging.getLogger("axor.backend")


def verify_license_str(license_json: str, vendor_pubkey: str) -> Any:  # noqa: ANN401
    """Verify a license file against the operator-pinned vendor key."""
    from axor_backend.ee.license import verify_license

    if not vendor_pubkey:
        raise ValueError("no AXOR_VENDOR_PUBKEY configured")
    return verify_license(license_json, vendor_pubkey)


async def load_licenses(state: Any) -> None:  # noqa: ANN401 - app.state is dynamic
    """Boot rehydrate, per tenant: the ``AXOR_LICENSE`` env (raw license-file
    JSON) applies to the public tenant, and each org's own pasted license comes
    from ITS row in the settings KV. Invalid or unverifiable licenses log a
    warning and leave EE off for that org — never crash boot, never entitle
    another tenant."""
    env_license = state.config.env_license
    for org in await state.store.list_orgs():
        set_current_org(org)
        raw = await state.store.get_setting("license_json")
        if org == PUBLIC_ORG and env_license:
            raw = env_license  # the operator's env pin wins for the local tenant
        if not raw:
            continue
        try:
            state.licenses[org] = verify_license_str(raw, state.config.vendor_pubkey)
        except Exception as exc:  # noqa: BLE001 - boot must not die on a bad license
            log.warning("stored license ignored for org %s: %s", org, exc)
    set_current_org(PUBLIC_ORG)


def stored_license(state: Any, org: str) -> Any | None:  # noqa: ANN401
    """The verified license held for one tenant, expired or not.

    `active_license` collapses "expired" into "absent", which is right for
    deciding whether a feature runs and wrong for telling the operator why. A
    customer whose renewal slipped by a day was told to add a license.
    """
    return getattr(state, "licenses", {}).get(org)


def today() -> str:
    return datetime.now(UTC).date().isoformat()


# How long before expiry the operator is told. Three warnings, not one: the
# first is a reminder, the last is an emergency, and a single notice sent 30
# days out is one an operator can miss entirely.
EXPIRY_NOTICE_DAYS = (30, 14, 7, 3, 1)


def days_until(expires_at: str, from_day: str | None = None) -> int | None:
    """Whole days from today to `expires_at`, negative once past. None when the
    date is not one — a license is vendor-signed, so a malformed date is a
    vendor bug, and guessing at it is worse than reporting nothing."""
    try:
        end = date.fromisoformat(expires_at)
        start = date.fromisoformat(from_day) if from_day else datetime.now(UTC).date()
    except (TypeError, ValueError):
        return None
    return (end - start).days


def active_license(state: Any, org: str) -> Any | None:  # noqa: ANN401
    """The verified, non-expired license OF ONE TENANT — or None. Expiry
    degrades EE to read-only (Line 1: safety never checks a license)."""
    lic = stored_license(state, org)
    if lic is None or lic.is_expired(today()):
        return None
    return lic


def expected_org(config: Any, org: str) -> str | None:  # noqa: ANN401
    """The organization name a license must carry here, or None if unbound.

    An operator pin (``AXOR_ORG``) wins: it is the only thing a single-tenant
    self-hosted install can be checked against. Otherwise an identity tenant is
    its own answer. The public tenant with no pin has no name to check against
    — that deployment is unbound, and `warn_about_open_posture` says so at boot
    rather than letting the field look like it means something.
    """
    if config.org:
        return config.org
    if org and org != PUBLIC_ORG:
        return org
    return None


def binding_error(lic: Any, config: Any, org: str) -> str | None:  # noqa: ANN401
    """Why this license does not belong to this deployment, or None.

    The `organization` field is inside the signed payload precisely so it can be
    checked; until it was, a license bought by one customer worked for every
    other one who obtained the file.
    """
    expected = expected_org(config, org)
    if expected is None or lic.organization == expected:
        return None
    return (
        f"this license is issued to {lic.organization!r}; this deployment is "
        f"licensed to {expected!r}. A license belongs to one organization."
    )


def require_ee(
    state: Any,  # noqa: ANN401
    org: str,
    what: str,
    *,
    min_tier: str = "team",
) -> None:
    """Gate a paid org feature by the license's workspace tier, not merely by a
    license being present (axor-packaging.md §1): a community-tier license does
    not unlock a team feature. Safety features never call this.

    The 402 names which of the three reasons applies. Two of them used to
    collapse: an expired license and no license at all produced the same "add a
    license", so a customer whose renewal slipped read that they had never
    bought one.
    """
    lic = stored_license(state, org)
    if lic is None:
        raise HTTPException(
            402,
            f"{what} is a paid org feature ({min_tier} tier) — add a license in "
            "Settings → LICENSE. Safety features never require one.",
        )
    if lic.is_expired(today()):
        raise HTTPException(
            402,
            f"{what} is a paid org feature and this license expired on "
            f"{lic.expires_at}. EE is read-only until it is renewed; safety "
            "features are untouched and never require a license.",
        )
    if not lic.tier_at_least(min_tier):
        raise HTTPException(
            402,
            f"{what} needs the {min_tier} workspace tier or higher; this license "
            f"is '{lic.workspace_tier}'.",
        )


def license_payload(lic: Any) -> dict:  # noqa: ANN401
    """The public shape of a license — what both /verify and /status report."""
    return {
        "organization": lic.organization,
        "workspace_tier": lic.workspace_tier,
        "governed_node_ceiling": lic.governed_node_ceiling,
        "self_hosted_runner": lic.self_hosted_runner,
        "expires_at": lic.expires_at,
        "features": list(lic.features),
    }


def billing_month(day: str | None = None) -> tuple[str, str]:
    """The inclusive UTC-day range of the calendar month `day` falls in.

    A calendar month because that is the unit an invoice is written in; a
    rolling window would make "this month's peak" mean something different on
    every day it was asked.
    """
    on = date.fromisoformat(day) if day else datetime.now(UTC).date()
    first = on.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return first.isoformat(), last.isoformat()


async def ceiling_status(state: Any, org: str) -> dict[str, Any]:  # noqa: ANN401
    """This month's fleet against this tenant's licensed ceiling.

    Never a refusal. A governed node is a safety surface, and safety never
    checks a license (Line 1) — so being over the ceiling is reported, loudly
    and repeatedly, and nothing is turned off.

    The number is the PEAK number of nodes that reported on any one day of the
    current billing month. It used to be `len(list_nodes())` — the union of
    desired and reported state, which contains no time and therefore never
    shrinks, so a customer who replaced one node was over their allowance
    forever, and the figure could not have been billed from either.
    """
    lic = active_license(state, org)
    start, end = billing_month()
    usage = await state.store.node_usage(start, end)
    peak = int(usage["peak_nodes"])
    common = {
        "billing_month": start[:7],
        "peak_nodes": peak,
        "peak_day": usage["peak_day"],
        "distinct_nodes": usage["distinct_nodes"],
    }
    if lic is None:
        return {**common, "governed_node_ceiling": None, "over_ceiling": False}
    return {
        **common,
        "governed_node_ceiling": lic.governed_node_ceiling,
        "over_ceiling": not lic.allows_nodes(peak),
    }


async def warn_over_ceiling(state: Any, org: str) -> None:  # noqa: ANN401
    """Log one warning per sweep for a tenant running more nodes than licensed."""
    status = await ceiling_status(state, org)
    if status["over_ceiling"]:
        log.warning(
            "org %s peaked at %d governed nodes on %s against a licensed ceiling "
            "of %d — governance is untouched (safety never checks a license); "
            "this is a billing discrepancy to settle with the vendor.",
            org, status["peak_nodes"], status["peak_day"],
            status["governed_node_ceiling"],
        )


async def notify_expiring(state: Any, org: str) -> None:  # noqa: ANN401
    """Tell the operator a license is running out, once per threshold crossed.

    Expiry used to be entirely silent: EE degraded to read-only and the first
    anyone heard of it was a 402 on a feature that had worked yesterday. The
    notification machinery already existed and carried nothing about the thing
    that pays for it.

    The bookkeeping is a stored high-water mark rather than a debounce, because
    the interesting event is crossing a threshold and there are only a handful
    of crossings in a license's life — a time-based debounce would either repeat
    daily or miss the 1-day notice after firing the 3-day one.
    """
    lic = stored_license(state, org)
    if lic is None:
        return
    left = days_until(lic.expires_at)
    if left is None or left > EXPIRY_NOTICE_DAYS[0]:
        return
    crossed = min((d for d in EXPIRY_NOTICE_DAYS if left <= d), default=None)
    if crossed is None:  # already expired: said once, at the 1-day mark
        crossed = 0
    already = await state.store.get_setting("license_expiry_notified")
    if isinstance(already, dict) and already.get("threshold") == crossed \
            and already.get("expires_at") == lic.expires_at:
        return
    await state.store.set_setting("license_expiry_notified", {
        "threshold": crossed, "expires_at": lic.expires_at,
    })
    await state.notifier.emit(
        "license_expiring", lic.organization,
        {"expires_at": lic.expires_at, "days_remaining": left,
         "workspace_tier": lic.workspace_tier, "expired": left < 0},
        org=org,
    )
    log.warning(
        "license for org %s expires on %s (%d days) — EE goes read-only when "
        "it lapses; safety features are untouched.",
        org, lic.expires_at, left,
    )


# ── renewal ───────────────────────────────────────────────────────────────────
#
# A monthly subscription against an OFFLINE license means a new file every
# month, pasted by hand, or the deployment quietly goes read-only. That is the
# cost of a check that never phones home, and it is worth paying for an
# air-gapped install and absurd for a hosted one.
#
# So renewal is a PULL, not an inbound webhook, and it is opt-in. The control
# plane fetches its next license the same way a governed node fetches its
# desired state: it dials out. A push would need the customer's backend to be
# reachable from the vendor's — an inbound write surface on a security product,
# behind their NAT, for a payment event that is not time-critical. The whole
# plane protocol is dial-out for this reason (protocol v0.2: zero listening
# sockets on customer infrastructure) and licensing does not get an exception.
#
# What comes back is verified by exactly the checks a pasted license passes,
# plus one more: the new expiry may not be EARLIER than the one in force. That
# makes a replayed old file useless — the only thing this endpoint can do is
# move a deployment forward, on a license the vendor signed for it.

RENEWAL_WINDOW_DAYS = 21


async def renewal_due(state: Any, org: str) -> bool:  # noqa: ANN401
    """Whether it is time to ask for the next license.

    Inside the window, or already lapsed — a lapsed deployment keeps asking,
    because the renewal it is waiting for is the one that brings EE back.
    """
    if not state.config.license_renewal_url:
        return False
    lic = stored_license(state, org)
    if lic is None:
        return False  # nothing to renew; a first license is pasted by a human
    left = days_until(lic.expires_at)
    return left is not None and left <= RENEWAL_WINDOW_DAYS


def renewal_rejection(
    new: Any, current: Any, config: Any, org: str,  # noqa: ANN401
) -> str | None:
    """Why a fetched license must not replace the one in force, or None.

    The signature says the vendor issued it. These say it is ours, it is not
    already dead on arrival, and it is not a replay of an older file — the last
    is what keeps a fetch from being a downgrade.
    """
    mismatch = binding_error(new, config, org)
    if mismatch:
        return mismatch
    if new.is_expired(today()):
        return f"the fetched license expired on {new.expires_at}"
    if current is not None and new.expires_at < current.expires_at:
        return (
            f"the fetched license expires on {new.expires_at}, before the one "
            f"in force ({current.expires_at}) — renewal only moves forward"
        )
    return None


async def renew_once(state: Any, org: str, *, fetch: Any = None) -> bool:  # noqa: ANN401
    """Fetch and install this tenant's next license. True when one was installed.

    Never raises and never removes an entitlement: every failure leaves the
    license in force exactly as it was, and the deployment degrades on its own
    schedule as if renewal had never been configured.
    """
    current = stored_license(state, org)
    getter = fetch or _fetch_license
    try:
        raw = await getter(state.config.license_renewal_url, org, current)
    except Exception as exc:  # noqa: BLE001 - an unreachable vendor is not an error here
        log.warning("license renewal fetch failed for org %s: %s", org, exc)
        return False
    if not raw:
        return False
    try:
        new = verify_license_str(raw, state.config.vendor_pubkey)
    except Exception as exc:  # noqa: BLE001 - untrusted response
        log.warning("fetched license for org %s did not verify: %s", org, exc)
        return False
    rejected = renewal_rejection(new, current, state.config, org)
    if rejected:
        log.warning("fetched license for org %s rejected: %s", org, rejected)
        return False
    await state.store.set_setting("license_json", raw)
    state.licenses[org] = new
    # the next expiry is a new subject; the old notice must not suppress it
    await state.store.set_setting("license_expiry_notified", None)
    log.info("license for org %s renewed through %s", org, new.expires_at)
    return True


async def _fetch_license(url: str, org: str, current: Any) -> str | None:  # noqa: ANN401
    """Ask the vendor for this deployment's current license.

    The request carries the license IN FORCE, which is what identifies the
    caller: it is vendor-signed and names the organization, so the vendor can
    answer without this deployment holding any additional credential. A CP that
    has never been licensed does not call at all.
    """
    import httpx  # noqa: PLC0415

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json={
            "organization": current.organization if current else None,
            "expires_at": current.expires_at if current else None,
            "org": org,
        })
    if response.status_code == 204:
        return None  # nothing newer
    response.raise_for_status()
    body = response.json()
    return body.get("license_json") if isinstance(body, dict) else None
