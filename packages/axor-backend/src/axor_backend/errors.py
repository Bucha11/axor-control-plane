"""Backend error types and the last-resort HTTP handler."""
from __future__ import annotations

import logging

from fastapi import Request
from fastapi.responses import JSONResponse


class BackendError(Exception):
    """Base for axor-backend errors."""


class CommandRejected(BackendError):
    """Operator command failed validation (bad signature, unknown node, malformed state)."""


class TraceNotFound(BackendError):
    pass


async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    """Log an unhandled route error with structure (and ship it to Sentry when
    configured) instead of letting it surface only as an opaque 500 in an
    access log.

    The response body deliberately does NOT carry ``str(exc)``. An unhandled
    exception here is most often a database error, and SQLAlchemy's message
    quotes the failing statement and the connection URL — which is how a 500
    turns into a disclosure of the schema and the DB path. The detail belongs
    in the operator's log; the caller gets the fact and nothing else.
    """
    logging.getLogger("axor.backend").error(
        "unhandled error on %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    return JSONResponse({"error": "internal"}, status_code=500)
