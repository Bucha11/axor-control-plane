"""Offline Ed25519 license verification (monetization doc section 4).

An Ed25519-signed license file — the same signature infrastructure the control
plane ships (protocol section 6). Offline-verifiable: no phone-home, no license
server, works air-gapped. For this audience, "our license check is the same
crypto that guards your command channel, and it never calls us" is itself a
selling point.

License encodes: org, tier, node ceiling, expiry. EE features check it at
startup; expiry degrades gracefully to read-only EE (never disables safety
features — Line 1). The canonical signed payload is JCS-subset JSON, identical
to plane commands, so one canonicalizer covers both.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from axor_backend.signing import jcs_canonical


class LicenseError(Exception):
    """License missing, malformed, badly signed, or from an untrusted vendor key."""


@dataclass(frozen=True)
class License:
    org: str
    tier: str  # "team" | "enterprise"
    node_ceiling: int
    expiry: str  # ISO date; compared lexicographically against `today`
    features: tuple[str, ...] = ()

    def is_expired(self, today: str) -> bool:
        return today > self.expiry

    def allows_nodes(self, count: int) -> bool:
        return count <= self.node_ceiling

    def enables(self, feature: str, today: str) -> bool:
        """A feature is enabled only under a non-expired license that lists it.
        After expiry EE goes read-only: no feature enables (but safety is
        untouched — safety never checks a license)."""
        return feature in self.features and not self.is_expired(today)


def _license_payload(lic: dict) -> bytes:
    return jcs_canonical({
        "org": lic["org"],
        "tier": lic["tier"],
        "node_ceiling": lic["node_ceiling"],
        "expiry": lic["expiry"],
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
            org=str(lic["org"]),
            tier=str(lic["tier"]),
            node_ceiling=int(lic["node_ceiling"]),
            expiry=str(lic["expiry"]),
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
