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
stored, active.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from axor_backend.deps import ConfigDep, StateDep, StoreDep
from axor_backend.licensing import active_license, license_payload
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1/license", tags=["license"])

_NO_PIN = (
    "this deployment pins no vendor public key, so a license signature cannot "
    "be checked against anything. Set AXOR_VENDOR_PUBKEY (see .env.example) and "
    "restart; the key is published with the distribution."
)
_WRONG_PIN = (
    "vendor_pubkey does not match the key this deployment pins. The trust root "
    "is deployment config (AXOR_VENDOR_PUBKEY), not request data — a signature "
    "checked against a key from the same request proves nothing. Omit the field "
    "to use the pinned key."
)


@router.post("/verify")
async def license_verify(
    body: dict, state: StateDep, store: StoreDep, config: ConfigDep
) -> dict:
    """Verify a license against the pinned vendor key, then activate it."""
    from axor_backend.ee.license import LicenseError, verify_license

    if not config.vendor_pubkey:
        raise HTTPException(400, _NO_PIN)
    # An older client echoes the pinned key back; that is harmless. A DIFFERENT
    # key is a request to move the trust root, which this route does not do.
    supplied = body.get("vendor_pubkey")
    if supplied and supplied != config.vendor_pubkey:
        raise HTTPException(400, _WRONG_PIN)
    try:
        lic = verify_license(body.get("license_json", ""), config.vendor_pubkey)
    except LicenseError as exc:
        raise HTTPException(403, str(exc)) from exc
    # Persist it (survives restarts) and hold it in state so org features unlock
    # immediately. Note this rewrites the deployment's entitlement, which is why
    # the route is `admin` and never open: anyone holding ANY vendor-signed
    # license could otherwise swap a paid tier for a community one and silently
    # switch EE features off.
    await store.set_setting("license_json", body.get("license_json", ""))
    state.licenses[current_org_id()] = lic
    # Node-ceiling telemetry (launch-readiness §5): compare the live fleet
    # against the license and WARN — never block; safety never checks a license
    # (monetization Line 1).
    live_nodes = len(await store.list_nodes())
    return {
        **license_payload(lic),
        "live_nodes": live_nodes,
        "over_ceiling": live_nodes > lic.governed_node_ceiling,
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
    """
    lic = active_license(state, current_org_id())
    configured = bool(config.vendor_pubkey)
    if lic is None:
        return {"active": False, "vendor_key_configured": configured}
    return {"active": True, "vendor_key_configured": configured, **license_payload(lic)}
