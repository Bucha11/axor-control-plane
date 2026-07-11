from __future__ import annotations


class BackendError(Exception):
    """Base for axor-backend errors."""


class CommandRejected(BackendError):
    """Operator command failed validation (bad signature, unknown node, malformed state)."""


class TraceNotFound(BackendError):
    pass
