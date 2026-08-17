"""Alembic environment — programmatic-only, same posture as axor-backend.

The service runs `upgrade head` itself at boot (storage.init_db) over the app's
own async engine, handing this env a live sync connection via
`config.attributes["connection"]`. There is no ini file and no CLI database URL:
the app's AXOR_IDENTITY_DATABASE_URL is the single source of truth.
"""
from __future__ import annotations

from alembic import context
from axor_identity.storage import metadata

target_metadata = metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "axor-identity migrations run programmatically at app boot "
        "(storage.init_db); there is no standalone CLI path."
    )

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    render_as_batch=connection.dialect.name == "sqlite",
)

with context.begin_transaction():
    context.run_migrations()
