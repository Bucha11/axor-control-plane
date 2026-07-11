"""Alembic environment — programmatic-only by design.

The backend runs `upgrade head` itself at boot (storage.init_db) over the
app's own async engine, handing this env a live sync connection via
`config.attributes["connection"]`. There is no ini file and no CLI database
URL: the app's AXOR_DATABASE_URL is the single source of truth, and migrations
can never run against a different database than the app uses.
"""
from __future__ import annotations

from alembic import context
from axor_backend.storage import metadata

target_metadata = metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError(
        "axor-backend migrations run programmatically at app boot "
        "(storage.init_db); there is no standalone CLI path."
    )

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    # SQLite can't ALTER most things in place; batch mode rewrites the table.
    render_as_batch=connection.dialect.name == "sqlite",
)

with context.begin_transaction():
    context.run_migrations()
