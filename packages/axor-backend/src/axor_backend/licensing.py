"""EE entitlement state (monetization §4): verified once, persisted, honest.

A license entitles ONE organization, so the verified licenses live in a dict
keyed by org rather than a process-wide slot — otherwise whichever tenant pasted
last would decide everyone else's tier.

The rule that shapes every function here: **safety never checks a license**
(monetization Line 1). :func:`require_ee` is called only by org-layer surfaces —
scheduled corpus CI, run history, notification routing. A gate, a degradation
level, a denial, a manual corpus run: never.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
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


def active_license(state: Any, org: str) -> Any | None:  # noqa: ANN401
    """The verified, non-expired license OF ONE TENANT — or None. Expiry
    degrades EE to read-only (Line 1: safety never checks a license)."""
    lic = getattr(state, "licenses", {}).get(org)
    if lic is None or lic.is_expired(datetime.now(UTC).date().isoformat()):
        return None
    return lic


def require_ee(
    state: Any,  # noqa: ANN401
    org: str,
    what: str,
    *,
    min_tier: str = "team",
    module: str | None = None,
) -> None:
    """Gate a paid org feature by the license's workspace tier (and optionally a
    module), not merely by a license being present (axor-packaging.md §1). A
    community-tier license does not unlock a team feature; the 402 names what is
    needed. Safety features never call this."""
    lic = active_license(state, org)
    if lic is None:
        raise HTTPException(
            402,
            f"{what} is a paid org feature ({min_tier} tier) — add a license in "
            "Settings → LICENSE. Safety features never require one.",
        )
    if not lic.tier_at_least(min_tier):
        raise HTTPException(
            402,
            f"{what} needs the {min_tier} workspace tier or higher; this license "
            f"is '{lic.workspace_tier}'.",
        )
    if module is not None and not lic.has_module(module):
        raise HTTPException(
            402,
            f"{what} needs the {module} module, which this license does not enable.",
        )


def license_payload(lic: Any) -> dict:  # noqa: ANN401
    """The public shape of a license — what both /verify and /status report."""
    from axor_backend.ee.license import KNOWN_MODULES

    return {
        "organization": lic.organization,
        "workspace_tier": lic.workspace_tier,
        "modules": {m: lic.has_module(m) for m in KNOWN_MODULES},
        "governed_node_ceiling": lic.governed_node_ceiling,
        "self_hosted_runner": lic.self_hosted_runner,
        "expires_at": lic.expires_at,
        "features": list(lic.features),
    }
