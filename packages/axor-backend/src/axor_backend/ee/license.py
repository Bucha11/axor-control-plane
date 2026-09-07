"""Offline Ed25519 license verification (axor-packaging.md §4).

An Ed25519-signed license file — the same signature infrastructure the control
plane ships (protocol section 6). Offline-verifiable: no phone-home, no license
server, works air-gapped. For this audience, "our license check is the same
crypto that guards your command channel, and it never calls us" is itself a
selling point.

One license carries the whole ladder (axor-packaging.md §4): the workspace tier
(community | team | security | enterprise), the governed-node ceiling, and
whether a self-hosted runner is licensed.

**One ladder, not one ladder plus a module matrix.** Private Lab and Control
Plane used to be separately licensed flags on top of a tier, so a paid customer
could hold a workspace without production governance or the reverse. They are
one product on one ladder now: a rung that entitles the Lab entitles the
Control Plane, and the reverse. The `modules` field is gone rather than pinned
to `{true, true}` — a field that cannot vary decides nothing, and this one was
read as though it did.

Expiry degrades EE to read-only; it never disables safety features (Line 1). The canonical signed
payload is JCS-subset JSON, identical to plane commands, so one canonicalizer
covers both.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from axor_backend.signing import jcs_canonical

# Workspace tiers, ordered — a higher tier includes everything below it, and
# every rung covers the whole product. `enterprise` is here because
# axor-identity has always had it: a hosted organization on the top plan
# carried `tier: enterprise`, which this table did not know, so
# `tier_at_least("team")` read -1 and the most expensive customer failed the
# cheapest gate. Two services, one vocabulary.
_TIER_ORDER = {"community": 0, "team": 1, "security": 2, "enterprise": 3}
TIERS = ("community", "team", "security", "enterprise")


class LicenseError(Exception):
    """License missing, malformed, badly signed, or from an untrusted vendor key."""


@dataclass(frozen=True)
class License:
    organization: str
    workspace_tier: str  # community | team | security | enterprise
    governed_node_ceiling: int
    expires_at: str  # ISO date; compared lexicographically against `today`
    self_hosted_runner: bool = False
    features: tuple[str, ...] = ()  # granular EE flags (e.g. sso, compliance_exports)

    # backward-compatible read aliases for the pre-ladder flat schema, so
    # existing readers (`lic.org`, `lic.tier`, …) keep working during the
    # pricing-page/entitlement normalization. The canonical names above are the
    # source of truth; these never appear in the signed payload.
    @property
    def org(self) -> str:
        return self.organization

    @property
    def tier(self) -> str:
        return self.workspace_tier

    @property
    def node_ceiling(self) -> int:
        return self.governed_node_ceiling

    @property
    def expiry(self) -> str:
        return self.expires_at

    def is_expired(self, today: str) -> bool:
        return today > self.expires_at

    def tier_at_least(self, tier: str) -> bool:
        """Whether the workspace tier is at least `tier` (community < team <
        security < enterprise) — the whole entitlement gate. A rung entitles
        the Lab and the Control Plane alike."""
        return _TIER_ORDER.get(self.workspace_tier, -1) >= _TIER_ORDER.get(tier, 99)

    def allows_nodes(self, count: int) -> bool:
        return count <= self.governed_node_ceiling

    def enables(self, feature: str, today: str) -> bool:
        """A granular feature is enabled only under a non-expired license that
        lists it. After expiry EE goes read-only: no feature enables (but safety
        is untouched — safety never checks a license)."""
        return feature in self.features and not self.is_expired(today)


def _license_payload(lic: dict) -> bytes:
    return jcs_canonical({
        "organization": lic["organization"],
        "workspace_tier": lic["workspace_tier"],
        "governed_node_ceiling": lic["governed_node_ceiling"],
        "self_hosted_runner": bool(lic.get("self_hosted_runner", False)),
        "expires_at": lic["expires_at"],
        "features": lic.get("features", []),
    })


def verify_license(license_json: str, vendor_pubkey_hex: str) -> License:
    """Parse and verify a license file against the vendor's Ed25519 public key.

    Raises LicenseError on any failure. Uses the same PyNaCl Ed25519 the plane
    service verifies commands with (protocol section 6) — one crypto stack. The
    vendor public key is compiled into the distribution (or operator-pinned); a
    license the vendor did not sign is rejected.
    """
    from nacl.exceptions import BadSignatureError
    from nacl.signing import VerifyKey

    try:
        data = json.loads(license_json)
        sig_hex = data["sig"]
        lic = data["license"]
        message = _license_payload(lic)
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise LicenseError(f"malformed license: {exc}") from exc

    try:
        VerifyKey(bytes.fromhex(vendor_pubkey_hex)).verify(
            message, bytes.fromhex(sig_hex)
        )
    except (BadSignatureError, ValueError) as exc:
        raise LicenseError("license signature invalid (not signed by the vendor)") from exc

    try:
        return License(
            organization=str(lic["organization"]),
            workspace_tier=str(lic["workspace_tier"]),
            governed_node_ceiling=int(lic["governed_node_ceiling"]),
            expires_at=str(lic["expires_at"]),
            self_hosted_runner=bool(lic.get("self_hosted_runner", False)),
            features=tuple(lic.get("features", ())),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LicenseError(f"malformed license fields: {exc}") from exc


def sign_license(lic: dict, vendor_privkey_hex: str) -> str:
    """Vendor-side helper (kept here so the test vector is reproducible): sign a
    license dict, return the license-file JSON {license, sig}."""
    from nacl.signing import SigningKey

    key = SigningKey(bytes.fromhex(vendor_privkey_hex))
    sig = key.sign(_license_payload(lic)).signature.hex()
    return json.dumps({"license": lic, "sig": sig}, sort_keys=True)
