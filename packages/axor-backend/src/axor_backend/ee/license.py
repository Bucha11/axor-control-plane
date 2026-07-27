"""Offline Ed25519 license verification (axor-packaging.md §4).

An Ed25519-signed license file — the same signature infrastructure the control
plane ships (protocol section 6). Offline-verifiable: no phone-home, no license
server, works air-gapped. For this audience, "our license check is the same
crypto that guards your command channel, and it never calls us" is itself a
selling point.

One license carries the whole ladder (axor-packaging.md §4): the workspace tier
(community | team | security), the enabled modules (private_lab, control_plane),
the governed-node ceiling, and whether a self-hosted runner is licensed. Modules
are FLAGS on one license, never separate licenses. Expiry degrades EE to
read-only; it never disables safety features (Line 1). The canonical signed
payload is JCS-subset JSON, identical to plane commands, so one canonicalizer
covers both.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from axor_backend.signing import jcs_canonical

# The modules the ladder recognizes (axor-packaging.md §0): Private Lab (the
# experiment/evidence workspace) and Control Plane (production enforcement).
KNOWN_MODULES = ("private_lab", "control_plane")
# Workspace tiers, ordered — a higher tier includes everything below it.
_TIER_ORDER = {"community": 0, "team": 1, "security": 2}


class LicenseError(Exception):
    """License missing, malformed, badly signed, or from an untrusted vendor key."""


def _enabled_modules(raw: object) -> tuple[str, ...]:
    """The enabled module names from either a ``{name: bool}`` object or a list
    of names — normalized to the canonical order, unknown names dropped."""
    if isinstance(raw, dict):
        return tuple(m for m in KNOWN_MODULES if bool(raw.get(m)))
    if isinstance(raw, (list, tuple)):
        named = {str(x) for x in raw}
        return tuple(m for m in KNOWN_MODULES if m in named)
    return ()


def _modules_payload(raw: object) -> dict[str, bool]:
    """The canonical, fixed-key ``{module: bool}`` object that gets signed — a
    stable key set (every known module, default false) so the signature does not
    depend on which modules the issuer happened to spell out."""
    enabled = _enabled_modules(raw)
    return {m: (m in enabled) for m in KNOWN_MODULES}


@dataclass(frozen=True)
class License:
    organization: str
    workspace_tier: str  # "community" | "team" | "security"
    modules: tuple[str, ...]  # enabled module names (subset of KNOWN_MODULES)
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

    def has_module(self, module: str) -> bool:
        """Whether this license enables a product module (private_lab /
        control_plane). A module a license does not carry stays locked."""
        return module in self.modules

    def tier_at_least(self, tier: str) -> bool:
        """Whether the workspace tier is at least `tier` (community < team <
        security) — the workspace-feature gate for Private Lab."""
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
        "modules": _modules_payload(lic.get("modules", {})),
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
            modules=_enabled_modules(lic.get("modules", {})),
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
