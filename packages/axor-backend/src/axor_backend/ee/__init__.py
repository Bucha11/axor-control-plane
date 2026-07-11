"""Enterprise (EE) features — source-visible, not free to use in production.

Licensing (monetization doc): the platform is Apache-2.0; this `/ee` subtree is
under a commercial license (GitLab pattern). EE code is auditable (security
buyers audit everything) but a valid license file is required to enable it in
production. The license check reuses the SAME Ed25519 infrastructure the
control plane already ships (protocol section 6): offline-verifiable, no
phone-home, works air-gapped.

Line 1 (safety) is never gated here: expiry degrades EE to read-only, it never
disables a safety feature. Nothing in this subtree touches a gate, a taint
decision, or a fail-closed default.
"""
from axor_backend.ee.license import (
    License,
    LicenseError,
    verify_license,
)

__all__ = ["License", "LicenseError", "verify_license"]
