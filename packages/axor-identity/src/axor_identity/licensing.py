"""The workspace's plan, as its vendor-signed license says — so the identity
service can tell which workspaces have single sign-on (the Business plan and
up) without asking the control plane.

The license is the same file the control plane activates (axor-backend
``ee/license.py``): ``{"license": {...}, "sig": hex}``, an Ed25519 signature
by the vendor over the canonical JSON of the license fields. Verified here
against the same pinned vendor key (``AXOR_VENDOR_PUBKEY``), bound to the
workspace it was issued to, and in date.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# the ladder, as the control plane has it (ee/license.py _TIER_ORDER)
ORDER = {"community": 0, "coding": 1, "business": 2, "team": 3, "security": 4, "enterprise": 5}


def rank(tier: str) -> int:
    """Where a plan sits on the ladder; -1 for none or unknown."""
    return ORDER.get(tier, -1)


class LicenseError(Exception):
    pass


@dataclass(frozen=True)
class License:
    organization: str
    tier: str
    expires_at: str


def _canonical(lic: dict) -> bytes:
    """RFC 8785 canonical JSON of the signed fields: strings, integers, a
    boolean and a list of strings, keys sorted — which plain sorted, compact
    JSON is, byte for byte."""
    fields = {
        "organization": lic["organization"],
        "workspace_tier": lic["workspace_tier"],
        "governed_node_ceiling": lic["governed_node_ceiling"],
        "self_hosted_runner": bool(lic.get("self_hosted_runner", False)),
        "expires_at": lic["expires_at"],
        "features": lic.get("features", []),
    }
    return json.dumps(fields, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode()


def verify(license_json: str, vendor_pubkey_hex: str) -> License:
    try:
        data = json.loads(license_json)
        lic, sig = data["license"], bytes.fromhex(data["sig"])
        message = _canonical(lic)
    except (ValueError, KeyError, TypeError) as exc:
        raise LicenseError(f"malformed license: {exc}") from exc
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(vendor_pubkey_hex)).verify(sig, message)
    except (InvalidSignature, ValueError) as exc:
        raise LicenseError("license signature invalid (not signed by the vendor)") from exc
    tier = str(lic["workspace_tier"])
    if tier not in ORDER:
        raise LicenseError(f"unknown plan {tier!r}")
    return License(str(lic["organization"]), tier, str(lic["expires_at"]))


def at_least(tier: str, expires_at: str | None, today: str, minimum: str) -> bool:
    """Whether a workspace on ``tier`` (until ``expires_at``) is at ``minimum``
    or above today. A plan past its date is the free one."""
    if expires_at and today > expires_at:
        return False
    return ORDER.get(tier, -1) >= ORDER.get(minimum, 99)
