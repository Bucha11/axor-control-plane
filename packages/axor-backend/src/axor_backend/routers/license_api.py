"""EE licensing (monetization §4).

Two routes with different jobs. ``/verify`` is a WRITE — it stores and activates
a license, changing the deployment's entitlement state, which is why it needs
`admin` and is never open. ``/status`` reports what is actually active, and is
what the UI trusts when deciding which org features to unlock.

**The vendor key is deployment config, not request data.** A license is an
ed25519 signature, and a signature is only worth what the key checking it is
worth — verifying one against a key supplied in the same request proves nothing
at all. So the trust root is pinned once, by the operator, in
``AXOR_VENDOR_PUBKEY``, and this module will not verify against anything else.

That rule replaced a subtler shape that read as safe and was not: the request
could carry its own ``vendor_pubkey``, the license verified against it, nothing
was stored, and the response came back with ``activated: false`` and every other
field populated — organization, tier, modules, node ceiling. A caller could
therefore hand the Settings panel a self-signed license and watch it render an
enterprise entitlement that did not exist. The same branch made the honest path
fail silently: with no env pin, an operator pasting the real vendor key got a
green summary and no activation, because activation required the pinned key that
was not set. One route, two ways to show an entitlement nobody held.

Now there is exactly one outcome for a 200: verified against the pinned key,
ISSUED TO THIS DEPLOYMENT, in date, stored, active — and stored for the tenant
it names rather than whichever one happened to be ambient.

The last two were missing, and each broke that sentence on its own. Expiry was
checked when a feature was USED and not when a license was stored, so a licence
that expired in 2020 came back 200 `activated: true` and `/status` immediately
said `active: false` — two answers to one question, in one sitting. And the
`organization` in the signed payload was compared to nothing at all, so one
purchased file activated in any tenant of any deployment; the name is signed
precisely so it can be checked.

`org` in the body is the hosted case. A tenant is confined to its own — an
identity login writes its own entitlement and nobody else's. But the operator
of a MULTI-TENANT deployment is not a tenant: the master token resolves to the
public organization, so an operator installing a customer's license got a 200
naming that customer and quietly wrote it to `public`, licensing the wrong
tenant with someone else's file and leaving the customer with nothing. An
operator now says which tenant, and the license still has to be issued to it.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Request

from axor_backend.deps import ConfigDep, PrincipalDep, StateDep, StoreDep
from axor_backend.ee.license import TIER_DEVELOPER_SEATS, TIER_NODE_CEILING
from axor_backend.ee.pricing import RATE_CARD, SEAT_PRICE, money, statement
from axor_backend.licensing import (
    active_license,
    billing_month,
    binding_error,
    ceiling_status,
    days_until,
    expected_org,
    license_payload,
    stored_license,
    today,
)
from axor_backend.tenancy import current_org_id, set_current_org

router = APIRouter(prefix="/v1/license", tags=["license"])

_NO_PIN = (
    "this deployment pins no vendor public key, so a license signature cannot "
    "be checked against anything. Set AXOR_VENDOR_PUBKEY (see .env.example) and "
    "restart; the key is published with the distribution."
)
_NOT_YOURS = (
    "only the deployment operator may install a license for another tenant; a "
    "login installs its own organization's license and no other."
)
_WRONG_PIN = (
    "vendor_pubkey does not match the key this deployment pins. The trust root "
    "is deployment config (AXOR_VENDOR_PUBKEY), not request data — a signature "
    "checked against a key from the same request proves nothing. Omit the field "
    "to use the pinned key."
)


def _target_org(body: dict, principal: object) -> str:
    """The tenant this license is being installed for.

    The caller's own, unless the OPERATOR names another. `kind == "master"` is
    the deployment operator, whose token is fleet-wide and therefore resolves to
    the public organization — which is exactly why they need to say which tenant
    rather than have `public` assumed for them.
    """
    named = body.get("org")
    if not named:
        return current_org_id()
    if getattr(principal, "kind", None) != "master":
        raise HTTPException(403, _NOT_YOURS)
    return str(named)


@router.post("/verify")
async def license_verify(
    body: dict, state: StateDep, store: StoreDep, config: ConfigDep,
    principal: PrincipalDep,
) -> dict:
    """Verify a license against the pinned vendor key, then activate it — for
    the calling tenant, or for the one an operator names."""
    from axor_backend.ee.license import LicenseError, verify_license

    if not config.vendor_pubkey:
        raise HTTPException(400, _NO_PIN)
    target = _target_org(body, principal)
    # An older client echoes the pinned key back; that is harmless. A DIFFERENT
    # key is a request to move the trust root, which this route does not do.
    supplied = body.get("vendor_pubkey")
    if supplied and supplied != config.vendor_pubkey:
        raise HTTPException(400, _WRONG_PIN)
    try:
        lic = verify_license(body.get("license_json", ""), config.vendor_pubkey)
    except LicenseError as exc:
        raise HTTPException(403, str(exc)) from exc
    # A signature proves the vendor issued this. It does not prove they issued
    # it to US, and it does not prove it is still in date. Both are checked
    # BEFORE anything is stored, so a 200 and `/status` cannot disagree.
    mismatch = binding_error(lic, config, target)
    if mismatch:
        raise HTTPException(403, mismatch)
    if lic.is_expired(today()):
        raise HTTPException(
            403,
            f"this license expired on {lic.expires_at} and was not activated. "
            "Renew it with the vendor; safety features never require one.",
        )
    # Persist it (survives restarts) and hold it in state so org features unlock
    # immediately. Note this rewrites the deployment's entitlement, which is why
    # the route is `admin` and never open: anyone holding ANY vendor-signed
    # license could otherwise swap a paid tier for a community one and silently
    # switch EE features off.
    set_current_org(target)
    await store.set_setting("license_json", body.get("license_json", ""))
    state.licenses[target] = lic
    # Node-ceiling telemetry (launch-readiness §5): compare the live fleet
    # against the license and WARN — never block; safety never checks a license
    # (monetization Line 1).
    return {
        **license_payload(lic),
        "org": target,
        **await ceiling_status(state, target),
        # Always true on a 200 now. Kept in the response because clients read
        # it, and because "verified" and "active" being the same thing is the
        # property worth stating.
        "activated": True,
    }


@router.get("/status")
async def license_status(state: StateDep, config: ConfigDep) -> dict:
    """The currently ACTIVE license (post-boot rehydrate) — what the UI uses to
    decide which org features to unlock vs render locked.

    ``vendor_key_configured`` is here so the UI can tell "no license yet" from
    "this deployment cannot check one", which are different problems with
    different fixes and used to look identical on screen.

    ``usage_reporting`` says whether the fleet size is volunteered to the vendor
    on the renewal call. `GET /v1/license/invoice` shows every field such a
    report would carry, and more — an operator can see exactly what would leave
    before deciding to let it.

    ``licensed_to`` is the organization this deployment accepts a license for,
    or null when it accepts any — the state a single-tenant install with no
    ``AXOR_ORG`` is in, which the operator should be able to see rather than
    assume. ``over_ceiling`` is recomputed here against the live fleet: it used
    to be answered once, at the moment a license was pasted, and never again.
    """
    org = current_org_id()
    lic = active_license(state, org)
    held = stored_license(state, org)
    base = {
        "vendor_key_configured": bool(config.vendor_pubkey),
        "licensed_to": expected_org(config, org),
        "auto_renewal": bool(config.license_renewal_url),
        # Whether this deployment volunteers its fleet size to the vendor.
        # Reported next to `auto_renewal` and separate from it, because they
        # are separate decisions: renewal is the vendor answering a question
        # about the deployment, reporting is the deployment telling the vendor
        # something about the customer.
        "usage_reporting": bool(config.usage_reporting),
    }
    if lic is None:
        # An expired license is still held, and saying so is the difference
        # between "renew this" and "buy one".
        if held is not None:
            return {"active": False, **base, **license_payload(held),
                    "days_remaining": days_until(held.expires_at),
                    "expired": True}
        return {"active": False, **base}
    return {
        "active": True, **base, **license_payload(lic),
        "days_remaining": days_until(lic.expires_at),
        "expired": False,
        **await ceiling_status(state, org),
    }


@router.get("/entitlements")
async def license_entitlements(state: StateDep, store: StoreDep) -> dict:
    """What the plan lets the plane show and operate for coding agents: on the
    free rung one developer and one agent family; the sessions and tree runs
    past that, locked, with the reason. An ingest key may read it — a tree run
    asks before it mixes agent families."""
    from axor_backend.ee.entitlements import coding_entitlements, workspace_tier

    tier = workspace_tier(active_license(state, current_org_id()))
    return await coding_entitlements(store, tier)


@router.put("/agent-family")
async def choose_agent_family(body: dict, request: Request, store: StoreDep,
                              principal: PrincipalDep) -> dict:
    """Which agent family the free plan keeps — Claude Code or Codex. Without
    a choice it is the first to report; this lets a workspace that started on
    one switch to the other. Sessions of the other family stay recorded and
    locked, and come back when the choice does. An owner or admin."""
    from axor_backend import permissions

    permissions.require_admin(principal)
    from axor_backend.ee.entitlements import FAMILY_SETTING, KNOWN_FAMILIES

    agent = body.get("agent")
    if agent not in KNOWN_FAMILIES:
        raise HTTPException(400, f"agent must be one of {list(KNOWN_FAMILIES)}")
    await store.set_setting(FAMILY_SETTING, agent)
    from axor_backend import audit

    await audit.record(store, request, "agent_family", agent)
    return {"agent_family": agent}


@router.get("/usage")
async def license_usage(state: StateDep, store: StoreDep, months: int = 3) -> dict:
    """Governed-node usage per billing month — what a per-node line is drawn from.

    The Control Plane could previously say how many nodes it had EVER seen and
    nothing else: `list_nodes()` is the union of desired and reported state,
    with no time in it, so the figure only ever grew. It could not be invoiced
    from, and as a ceiling check it was a ratchet — replace one node and you are
    over your allowance for good.

    Each month reports its PEAK (the most nodes on any one day, and which day)
    and its DISTINCT count. The peak is the billing basis: a fleet is as big as
    it ever ran, and the customer can point at the day. Distinct exceeds it
    whenever nodes are replaced rather than added, which is exactly why it must
    not be the invoice — a fleet of five recycled daily would bill as a hundred
    and fifty.

    Reported, never enforced. Being over the ceiling is a conversation with the
    vendor; it never turns governance off, because safety never checks a
    license.
    """
    span = max(1, min(int(months), 24))
    lic = active_license(state, current_org_id())
    ceiling = lic.governed_node_ceiling if lic else None
    # The UTC calendar, not the host's. `clock.today` says why: usage is metered
    # per UTC day "so that a fleet spanning time zones is counted once, on one
    # calendar, and a customer and the vendor reading the same invoice see the
    # same days" — and `plane.record_node_activity` stamps every row with it.
    # `date.today()` is the host's local day, so on any deployment east or west
    # of UTC the window and the rows it selects were on different calendars for
    # part of every day.
    on = date.fromisoformat(today())
    periods = []
    for _ in range(span):
        start, end = billing_month(on.isoformat())
        usage = await store.node_usage(start, end)
        devs = await store.developer_usage(start, end)
        periods.append({
            "month": start[:7],
            "peak_nodes": usage["peak_nodes"],
            "peak_day": usage["peak_day"],
            "distinct_nodes": usage["distinct_nodes"],
            # the coding-agent meter: developers whose agents reported
            "active_developers": devs["active_developers"],
            "days": usage["days"],
            "over_ceiling": ceiling is not None and usage["peak_nodes"] > ceiling,
        })
        on = date.fromisoformat(start) - timedelta(days=1)
    seats = TIER_DEVELOPER_SEATS.get(lic.workspace_tier) if lic else None
    return {"governed_node_ceiling": ceiling, "developer_seats": seats, "months": periods}


BILLING_HISTORY = 6


async def _month_statement(store: StoreDep, month: str, org_name: str, tier: str,
                           included_nodes: int) -> dict:
    from axor_backend.ee.seats import month_seats

    start, end = billing_month(f"{month}-01")
    usage = await store.node_usage(start, end)
    # a developer past the workspace's seat limit is not shown, not operated
    # — and not billed
    seats = await month_seats(store, month, tier)
    line = statement(month=month, organization=org_name, workspace_tier=tier,
                     included_nodes=included_nodes, peak_nodes=int(usage["peak_nodes"]),
                     peak_day=usage["peak_day"],
                     included_developers=TIER_DEVELOPER_SEATS.get(tier, 0),
                     active_developers=len(seats["seated"]))
    return {**line.as_dict(), "developers": seats["developers"],
            "seat_cap": seats["cap"], "over_cap": len(seats["over"])}


FORECAST_LOOKBACK_DAYS = 30


async def _forecast(store: StoreDep, current: dict, tier: str, included_nodes: int) -> dict:
    """Where this month's bill is heading: the developers counted so far, and
    those who ran agents in the last 30 days but not yet this month — they
    usually do again. Capped by the seat limit. An estimate, said so."""
    on = date.fromisoformat(today())
    recent = await store.developer_usage(
        (on - timedelta(days=FORECAST_LOOKBACK_DAYS)).isoformat(), on.isoformat())
    counted = {d["developer"] for d in current["developers"]}
    likely = [{"developer": d["developer"], "last_day": d.get("last_day")}
              for d in recent["developers"] if d["developer"] not in counted]
    so_far = current["active_developers"]
    cap = current.get("seat_cap")
    projected = so_far + len(likely)
    if cap is not None:
        projected = min(projected, max(cap, so_far))
    line = statement(month=current["month"], organization=current.get("organization", ""),
                     workspace_tier=tier, included_nodes=included_nodes,
                     peak_nodes=int(current["peak_nodes"]), peak_day=current.get("peak_day"),
                     included_developers=TIER_DEVELOPER_SEATS.get(tier, 0),
                     active_developers=projected).as_dict()
    return {"developers_so_far": so_far, "likely": likely,
            "projected_developers": projected,
            "projected_total_cents": line["total_cents"], "projected_total": line["total"],
            "seat_cap": cap, "seats_left": None if cap is None else max(0, cap - so_far),
            "next_seat_cents": SEAT_PRICE.get(tier, 0)
            if so_far >= TIER_DEVELOPER_SEATS.get(tier, 0) else 0}


@router.put("/seat-cap")
async def set_seat_cap(body: dict, request: Request, state: StateDep, store: StoreDep,
                       principal: PrincipalDep) -> dict:
    """An admin's limit on developer seats (``{"cap": n}``, or ``null`` for
    none). Past it a developer's sessions are protected but not shown,
    operated or billed. It cannot go below the seats this month already used:
    that work was done, and is billed. An owner or admin."""
    from axor_backend import permissions

    permissions.require_admin(principal)
    from axor_backend.ee.seats import MAX_CAP, SEAT_CAP_SETTING, month_seats

    lic = active_license(state, current_org_id())
    tier = lic.workspace_tier if lic else "community"
    if tier == "community":
        raise HTTPException(400, "the free plan is one developer; a seat limit is for a paid plan")
    cap = body.get("cap")
    from axor_backend import audit

    if cap is None:
        await store.set_setting(SEAT_CAP_SETTING, None)
        await audit.record(store, request, "seat_limit_removed", "")
        return {"seat_cap": None}
    if not isinstance(cap, int) or isinstance(cap, bool) or not 1 <= cap <= MAX_CAP:
        raise HTTPException(400, f"cap is a whole number of seats, 1 to {MAX_CAP}, or null")
    month = today()[:7]
    used = len((await month_seats(store, month, tier))["seated"])
    if cap < used:
        raise HTTPException(409, {"error": "below_used", "used": used,
                                  "detail": f"{used} developers already ran agents this month — "
                                            f"the limit can be {used} or more now, and lower "
                                            "from next month"})
    await store.set_setting(SEAT_CAP_SETTING, {"cap": cap, "since": month})
    await audit.record(store, request, "seat_limit", str(cap))
    return {"seat_cap": cap, "used": used}


@router.get("/billing")
async def license_billing(state: StateDep, store: StoreDep) -> dict:
    """Everything about money in one answer, for both journeys: the plan and
    what it includes, this month so far (provisional), the closed months'
    statements — and, on the free plan, what this month would cost on each
    paid rung, from this workspace's own usage. Statements are the same
    arithmetic as /invoice (ee.pricing); nothing here charges anyone."""
    org = current_org_id()
    lic = active_license(state, org) or stored_license(state, org)
    tier = lic.workspace_tier if lic else "community"
    this_month = date.fromisoformat(today()).strftime("%Y-%m")
    rate = RATE_CARD.get(tier)
    plan = {
        "tier": tier, "licensed": lic is not None,
        "organization": lic.organization if lic else "",
        "expires_at": lic.expires_at if lic else None,
        "base_cents": rate[0] if rate else None, "base": money(rate[0]) if rate else None,
        "per_node_cents": rate[1] if rate else None,
        "seat_cents": SEAT_PRICE.get(tier), "seat": money(SEAT_PRICE.get(tier, 0)),
        "included_nodes": lic.governed_node_ceiling if lic else TIER_NODE_CEILING["community"],
        "included_developers": TIER_DEVELOPER_SEATS.get(tier, 0),
        "priced": rate is not None,
    }
    out: dict = {"plan": plan, "current": None, "history": [], "if_paid": [], "forecast": None}
    if lic is not None:
        name = lic.organization
        out["current"] = await _month_statement(store, this_month, name, tier,
                                                lic.governed_node_ceiling)
        out["forecast"] = await _forecast(store, out["current"], tier, lic.governed_node_ceiling)
        on = date.fromisoformat(f"{this_month}-01")
        for _ in range(BILLING_HISTORY):
            on = (on - timedelta(days=1)).replace(day=1)
            out["history"].append(await _month_statement(
                store, on.strftime("%Y-%m"), name, tier, lic.governed_node_ceiling))
    else:
        # what this month would cost on each paid rung — the user's own usage,
        # the list prices; an estimate, said so
        for rung in ("coding", "team", "security"):
            est = await _month_statement(store, this_month, "", rung, TIER_NODE_CEILING[rung])
            out["if_paid"].append({"tier": rung, **est})
    return out


@router.get("/invoice")
async def license_invoice(
    state: StateDep, store: StoreDep, month: str | None = None,
) -> dict:
    """The statement for one month: the rung's price, the fleet past its
    allowance, and the evidence both are drawn from.

    Defaults to the month just ended, because that is the one there is a bill
    for. A month still running is returned marked `provisional` — its peak can
    only rise — so the number is never mistaken for a final one.

    The allowance is the license's OWN ceiling rather than the rung's list
    allowance, so a customer who negotiated 200 nodes is billed against 200.
    A contracted rung is measured and not totalled: it has no list price, and
    inventing one would put a figure nobody agreed to in front of a customer.
    """
    org = current_org_id()
    lic = active_license(state, org) or stored_license(state, org)
    if lic is None:
        raise HTTPException(
            404,
            "no license for this organization, so there is nothing to bill. "
            "Usage is still measured — see /v1/license/usage.",
        )
    if month is None:
        # Same calendar as the rows, for the same reason — and here it decides
        # WHICH MONTH is billed. At 2026-03-01T00:30+13:00 the host's day is
        # 2026-03-01 and the UTC day is 2026-02-28, so "the month just ended"
        # was February by the host and January by the meter: the default put a
        # different month's statement in front of the customer for thirteen
        # hours around every month boundary.
        first_of_this = date.fromisoformat(today()).replace(day=1)
        month = (first_of_this - timedelta(days=1)).strftime("%Y-%m")
    try:
        start, end = billing_month(f"{month}-01")
    except ValueError as exc:
        raise HTTPException(400, f"month must be YYYY-MM: {exc}") from exc
    from axor_backend.ee.seats import month_seats

    usage = await store.node_usage(start, end)
    seats = await month_seats(store, month, lic.workspace_tier)
    devs = {"developers": seats["developers"]}
    line = statement(
        month=month,
        organization=lic.organization,
        workspace_tier=lic.workspace_tier,
        included_nodes=lic.governed_node_ceiling,
        peak_nodes=int(usage["peak_nodes"]),
        peak_day=usage["peak_day"],
        included_developers=TIER_DEVELOPER_SEATS.get(lic.workspace_tier, 0),
        active_developers=len(seats["seated"]),
    )
    return {
        **line.as_dict(),
        # the days behind the peak travel WITH the statement: a customer asked
        # to pay for a peak is entitled to see the day it happened without
        # having to ask a second endpoint for it
        "evidence": {
            "days": usage["days"],
            "distinct_nodes": usage["distinct_nodes"],
            "developers": devs["developers"],
        },
    }
