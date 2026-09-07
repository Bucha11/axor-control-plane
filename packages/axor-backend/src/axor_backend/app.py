"""FastAPI application factory.

The backend persists and fans out; it never interprets governance — that is the
kernel's job (repo README). This module is only the assembly: resolve the
configuration, construct the collaborators the deployment will hold for its
lifetime, install the auth gate, and register the routers.

Where everything went:

===========================  ==================================================
:mod:`~axor_backend.config`      every ``AXOR_*`` variable, resolved once
:mod:`~axor_backend.security`    who is calling, and may they
:mod:`~axor_backend.deps`        request-scoped access to the collaborators below
:mod:`~axor_backend.lifecycle`   boot, shutdown, and the background sweeps
:mod:`~axor_backend.licensing`   EE entitlement state and the paid-feature gate
:mod:`~axor_backend.routers`     the HTTP surface, one module per domain
===========================  ==================================================

The state built here is deliberately small, and every field is something with a
lifetime longer than one request:

``store``
    the system of record.
``broadcast``
    the in-process SSE bus.
``graphs``
    the taint/provenance graph, ONE PER TENANT (spec decision 6). In-memory by
    default — the same dev posture as SQLite; a hosted deployment passes a
    factory returning ``KuzuGraphStore`` behind the same ``GraphStore``
    protocol. A single process-wide store would hand one tenant's value refs and
    run ids to every other tenant that guessed a ref.
``licenses``
    verified EE licenses per organization. A license entitles ONE tenant, so a
    process-wide slot would let whichever org pasted last decide everyone else's
    tier.
``shares``, ``notifier``, ``subgraph_cache``
    share links, webhook subscriptions, and derived causal subgraphs.

All of it lives in one process on purpose: ``graphs``, ``broadcast``, ``shares``
and ``notifier`` are per-process, so a second worker would hold a second,
divergent copy of each (see docs/ops-limits.md).
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from axor_backend.broadcast import Broadcast
from axor_backend.clock import now
from axor_backend.config import AppConfig
from axor_backend.errors import unhandled_error
from axor_backend.graph import GraphRegistry
from axor_backend.lifecycle import lifespan
from axor_backend.limits import SubgraphCache
from axor_backend.notifications import Notifier
from axor_backend.observability import setup_observability
from axor_backend.routers import ALL_ROUTERS
from axor_backend.security import auth_middleware
from axor_backend.share import ShareRegistry
from axor_backend.signing import OperatorKeyring
from axor_backend.storage import Store, make_engine


def create_app(
    database_url: str | None = None,
    operator_keys: dict[str, str] | None = None,
    allow_unsigned: bool | None = None,
    api_token: str | None = None,
    retention_days: float | None = None,
    vault_creds_token: str | None = None,
    vault_signing_token: str | None = None,
    identity_jwks: dict[str, Any] | None = None,
    identity_issuer: str = "axor-identity",
    vendor_pubkey: str | None = None,
    org: str | None = None,
    license_renewal_url: str | None = None,
    usage_reporting: bool | None = None,
) -> FastAPI:
    """Build a backend. Arguments win over the environment; see `AppConfig`."""
    config = AppConfig.resolve(
        database_url=database_url,
        operator_keys=operator_keys,
        allow_unsigned=allow_unsigned,
        api_token=api_token,
        retention_days=retention_days,
        vault_creds_token=vault_creds_token,
        vault_signing_token=vault_signing_token,
        identity_jwks=identity_jwks,
        identity_issuer=identity_issuer,
        vendor_pubkey=vendor_pubkey,
        org=org,
        license_renewal_url=license_renewal_url,
        usage_reporting=usage_reporting,
    )
    setup_observability()

    app = FastAPI(title="axor-backend", lifespan=lifespan)
    app.state.config = config
    app.state.store = Store(make_engine(config.database_url))
    app.state.broadcast = Broadcast()
    app.state.keyring = OperatorKeyring(config.operator_keys or {})
    app.state.allow_unsigned = config.allow_unsigned
    app.state.graphs = GraphRegistry()
    app.state.shares = ShareRegistry()
    app.state.subgraph_cache = SubgraphCache()
    app.state.licenses = {}
    # (org, node) -> the UTC day already written to the governed-node meter, so
    # a heartbeat every ten seconds is not a write every ten seconds. Per app,
    # not per process: a module global would outlive the store it describes.
    app.state.active_today = {}
    app.state.notifier = _notifier(app)

    app.add_exception_handler(Exception, unhandled_error)
    app.middleware("http")(auth_middleware)
    for router in ALL_ROUTERS:
        app.include_router(router)
    return app


def _notifier(app: FastAPI) -> Notifier:
    """A notifier that persists what it could not deliver.

    The dead-letter log is the honesty half of at-least-once delivery: a
    notification system that fails silently is worse than none, so a lost
    delivery is written down rather than kept in a process that may restart.
    """
    async def persist_dead_letter(letter: Any) -> None:  # noqa: ANN401 - DeadLetter
        await app.state.store.add_dead_letter(
            letter.url, letter.payload, letter.error, letter.attempts, now(),
            org=letter.org,
        )

    return Notifier(
        dead_sink=persist_dead_letter,
        block_private=app.state.config.webhook_block_private,
    )
