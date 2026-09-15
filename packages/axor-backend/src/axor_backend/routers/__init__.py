"""HTTP surface, one module per domain.

``ALL_ROUTERS`` is the single registration point: :func:`axor_backend.app.create_app`
includes exactly this list, in this order. Two of the routers do not live here —
``plane`` and ``wrap_api`` are domain modules that own their own router, and
moving them would rename the "plane service" every document in the repo calls
``axor_backend.plane``. They are still registered from here, so there remains
one list of everything this backend serves.

Order matters only where paths could shadow one another; it otherwise follows
the request's own journey — a trace arrives, is analysed, is proved against the
corpus, and is published.
"""
from __future__ import annotations

from fastapi import APIRouter

from axor_backend import plane, wrap_api
from axor_backend.routers import (
    analysis,
    corpus_api,
    demo,
    health,
    keys,
    lab,
    license_api,
    notifications_api,
    provenance_api,
    runs,
    share_api,
    vault,
)

ALL_ROUTERS: tuple[APIRouter, ...] = (
    plane.router,
    wrap_api.router,
    runs.router,
    demo.router,
    analysis.router,
    vault.router,
    lab.router,
    corpus_api.router,
    provenance_api.router,
    notifications_api.router,
    share_api.router,
    license_api.router,
    keys.router,
    health.router,
)

__all__ = ["ALL_ROUTERS"]
