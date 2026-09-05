"""EE licensing (monetization §4).

Two routes with different jobs: ``/verify`` is a WRITE — it stores and activates
a license, changing the deployment's entitlement state, which is why it needs
`admin` and is never open. ``/status`` reports what is actually active, and is
what the UI should trust when deciding which org features to unlock.

Only a license verified against the operator-pinned vendor key
(``AXOR_VENDOR_PUBKEY``) is ever stored. A caller-supplied key still verifies
the file's signature — that is the preview path — but activates nothing, and the
response says so in ``activated``.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from axor_backend.deps import ConfigDep, StateDep, StoreDep
from axor_backend.licensing import active_license, license_payload
from axor_backend.tenancy import current_org_id

router = APIRouter(prefix="/v1/license", tags=["license"])


@router.post("/verify")
async def license_verify(
    body: dict, state: StateDep, store: StoreDep, config: ConfigDep
) -> dict:
    from axor_backend.ee.license import LicenseError, verify_license

    supplied = body.get("vendor_pubkey")
    vendor_key = supplied or config.vendor_pubkey
    if not vendor_key:
        raise HTTPException(400, "no vendor public key configured")
    try:
        lic = verify_license(body.get("license_json", ""), vendor_key)
    except LicenseError as exc:
        raise HTTPException(403, str(exc)) from exc
    # A verified license ACTIVATES EE: persist it (survives restarts) and hold
    # it in state so org features unlock immediately. Activation happens only
    # against the PINNED key — this route used to be open on the grounds that
    # it was "a pure utility over user-supplied input"; it is not pure, it
    # rewrites the deployment's entitlement, so anyone holding ANY vendor-signed
    # license could swap a paid tier for a community one and silently switch EE
    # features off.
    pinned = not supplied or supplied == config.vendor_pubkey
    if pinned:
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
        "activated": pinned,
    }


@router.get("/status")
async def license_status(state: StateDep) -> dict:
    """The currently ACTIVE license (post-boot rehydrate) — what the UI uses to
    decide which org features to unlock vs render locked."""
    lic = active_license(state, current_org_id())
    if lic is None:
        return {"active": False}
    return {"active": True, **license_payload(lic)}
