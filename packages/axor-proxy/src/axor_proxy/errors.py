from __future__ import annotations


class ProxyError(Exception):
    """Base for axor-proxy errors."""


class UpstreamUnreachable(ProxyError):
    """Declared tool endpoint did not answer the pre-flight ping (onboarding step 3)."""
