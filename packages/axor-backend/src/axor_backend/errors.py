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


class ConfigInvalid(BackendError):
    """A kernel config (Config Builder shape or direct) is malformed.

    Its own type because it is a caller mistake, not a server fault: every
    consumer of ``kernel_config_from_json`` answers on behalf of an operator
    editing JSON by hand, so the field that is wrong belongs in the response.
    Mapped to 400 by :func:`config_invalid` — a 500 here reads as "Axor is
    broken" for what is a typo in a text box.
    """


class RunTooLarge(BackendError):
    """A batch would push one run past ``limits.MAX_EVENTS_PER_RUN``.

    Its own type, and raised from the store rather than the route, because only
    the store knows what is already there — and it has to know inside the same
    transaction, or two concurrent batches both measure a run that fits and both
    write. Mapped to 413 by :func:`run_too_large`: the request is legal, the run
    it would extend is not, and the client is the only party who can do anything
    about it (start a new run id) while there is still time to.
    """


class StaleVersion(BackendError):
    """A versioned write named a version the row no longer holds.

    Distinct from :class:`ConcurrentUpdate`: that one is contention the store
    retries through, this one is a command the store must NOT retry. An operator
    command carries an ed25519 signature over (node_id, version, delta,
    timestamp), so applying it at a different version would store a state whose
    audit record does not match what was signed. The operator re-signs instead.
    """


class ConcurrentUpdate(BackendError):
    """A versioned row was changed by someone else while we were editing it.

    Raised only after the read-modify-write has been retried and lost every
    time, which for desired state means sustained contention on one node. The
    caller must surface it — silently returning would be the lost update this
    exists to prevent.
    """


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


async def config_invalid(request: Request, exc: Exception) -> JSONResponse:
    """A malformed kernel config is the caller's, with the field named."""
    return JSONResponse({"error": str(exc)}, status_code=400)


async def run_too_large(request: Request, exc: Exception) -> JSONResponse:
    """A full run is the caller's problem too, and the message says the remedy."""
    return JSONResponse({"error": str(exc)}, status_code=413)
