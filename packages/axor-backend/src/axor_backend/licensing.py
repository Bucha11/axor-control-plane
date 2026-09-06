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
* **Does it cover this?** ``modules`` is signed and reported and gated nothing:
  every ``require_ee`` call omitted ``module=``, so a licence with
  ``control_plane: false`` opened the Control Plane.
* **How much of it?** ``allows_nodes`` existed and was called from nowhere, and
  ``over_ceiling`` was computed once, at paste time. Over-ceiling never blocks —
  a governed node is safety — but it must be visible, so it is recomputed on the
  housekeeping sweep and reported by ``/status``.
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


def stored_license(state: Any, org: str) -> Any | None:  # noqa: ANN401
    """The verified license held for one tenant, expired or not.

    `active_license` collapses "expired" into "absent", which is right for
    deciding whether a feature runs and wrong for telling the operator why. A
    customer whose renewal slipped by a day was told to add a license.
    """
    return getattr(state, "licenses", {}).get(org)


def today() -> str:
    return datetime.now(UTC).date().isoformat()


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
    module: str | None = None,
) -> None:
    """Gate a paid org feature by the license's workspace tier and module, not
    merely by a license being present (axor-packaging.md §1). A community-tier
    license does not unlock a team feature, and a license that does not carry a
    module does not unlock it. Safety features never call this.

    The 402 names which of the four reasons applies. They used to collapse: an
    expired license and no license at all produced the same "add a license",
    so a customer whose renewal slipped read that they had never bought one.
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


async def ceiling_status(state: Any, org: str) -> dict[str, Any]:  # noqa: ANN401
    """The live fleet against this tenant's licensed ceiling.

    Never a refusal. A governed node is a safety surface, and safety never
    checks a license (Line 1) — so being over the ceiling is reported, loudly
    and repeatedly, and nothing is turned off. `allows_nodes` existed for this
    and was called from nowhere; `over_ceiling` was computed once, at the moment
    a license was pasted, so a fleet that grew afterwards was never looked at
    again.
    """
    lic = active_license(state, org)
    live = len(await state.store.list_nodes())
    if lic is None:
        return {"live_nodes": live, "governed_node_ceiling": None,
                "over_ceiling": False}
    return {
        "live_nodes": live,
        "governed_node_ceiling": lic.governed_node_ceiling,
        "over_ceiling": not lic.allows_nodes(live),
    }


async def warn_over_ceiling(state: Any, org: str) -> None:  # noqa: ANN401
    """Log one warning per sweep for a tenant running more nodes than licensed."""
    status = await ceiling_status(state, org)
    if status["over_ceiling"]:
        log.warning(
            "org %s runs %d governed nodes against a licensed ceiling of %d — "
            "governance is untouched (safety never checks a license); this is a "
            "billing discrepancy to settle with the vendor.",
            org, status["live_nodes"], status["governed_node_ceiling"],
        )
