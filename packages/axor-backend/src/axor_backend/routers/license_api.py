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

from fastapi import APIRouter, HTTPException

from axor_backend.deps import ConfigDep, PrincipalDep, StateDep, StoreDep
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
    on = date.today()
    periods = []
    for _ in range(span):
        start, end = billing_month(on.isoformat())
        usage = await store.node_usage(start, end)
        periods.append({
            "month": start[:7],
            "peak_nodes": usage["peak_nodes"],
            "peak_day": usage["peak_day"],
            "distinct_nodes": usage["distinct_nodes"],
            "days": usage["days"],
            "over_ceiling": ceiling is not None and usage["peak_nodes"] > ceiling,
        })
        on = date.fromisoformat(start) - timedelta(days=1)
    return {"governed_node_ceiling": ceiling, "months": periods}
