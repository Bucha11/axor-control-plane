"""Request-scoped dependencies.

The backend keeps its long-lived collaborators on ``app.state``: the store, the
event bus, the share registry, the notifier, the resolved config. Handlers used
to reach for them through ``request.app.state.x``, which meant every route
signature carried a ``Request`` it did not otherwise want, and no route declared
what it actually needed.

These are the declarations. A handler asks for ``store: StoreDep`` and that is
its whole dependency list — readable at the signature, and overridable in a test
with ``app.dependency_overrides``.

Dependencies run after the auth middleware has stamped the request's org, so
anything tenant-scoped resolved here already sees the right ContextVar.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Request

from axor_backend.broadcast import Broadcast
from axor_backend.config import AppConfig
from axor_backend.limits import SubgraphCache
from axor_backend.notifications import Notifier
from axor_backend.storage import Store


def get_state(request: Request) -> Any:  # noqa: ANN401 - app.state is dynamic
    """The whole application state. For the few callers that legitimately need
    more than one collaborator at once — the license gate, mainly."""
    return request.app.state


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_principal(request: Request) -> Any:  # noqa: ANN401 - auth.Principal | None
    """Who is calling, as `security.auth_middleware` resolved them, or None on
    an open deployment. A route needs this only when the ANSWER differs by
    caller rather than by tenant — the tenant is already ambient."""
    return getattr(request.state, "principal", None)


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_broadcast(request: Request) -> Broadcast:
    return request.app.state.broadcast


def get_notifier(request: Request) -> Notifier:
    return request.app.state.notifier


def get_subgraph_cache(request: Request) -> SubgraphCache:
    return request.app.state.subgraph_cache


StateDep = Annotated[Any, Depends(get_state)]
ConfigDep = Annotated[AppConfig, Depends(get_config)]
PrincipalDep = Annotated[Any, Depends(get_principal)]
StoreDep = Annotated[Store, Depends(get_store)]
BroadcastDep = Annotated[Broadcast, Depends(get_broadcast)]
NotifierDep = Annotated[Notifier, Depends(get_notifier)]
SubgraphCacheDep = Annotated[SubgraphCache, Depends(get_subgraph_cache)]
