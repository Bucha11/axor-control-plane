"""Per-request tenant (organization) scope.

The control-plane is multi-tenant: a request authenticated as an axor-identity
user carries an `org`, and the data it reads and writes must be that org's and
no other. Rather than thread an org argument through every store call, the
auth layer stamps the current request's org into a ContextVar and the Store
reads it when it builds each query — the same "current tenant is ambient to the
request" shape the Lab uses.

`PUBLIC_ORG` is the single implicit tenant for everything that is not an
identity login: an open (no-auth) deployment, the operator master token, and
API keys minted without an org. A single-tenant deployment therefore behaves
exactly as before — all its data lives under one org and every request sees it.
"""
from __future__ import annotations

from contextvars import ContextVar

PUBLIC_ORG = "public"

_current_org: ContextVar[str] = ContextVar("axor_current_org", default=PUBLIC_ORG)


def set_current_org(org: str | None) -> None:
    """Set the org for the current request (None → the public tenant)."""
    _current_org.set(org or PUBLIC_ORG)


def current_org_id() -> str:
    return _current_org.get()
