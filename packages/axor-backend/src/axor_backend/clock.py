"""The one wall clock.

Timestamps go into the database and into notification payloads as ISO-8601 UTC
strings. Three modules had their own private ``_now``; a single definition means
a deployment cannot end up with two timestamp formats in one table.
"""
from __future__ import annotations

from datetime import UTC, datetime


def now() -> str:
    """The current instant as an ISO-8601 UTC string."""
    return datetime.now(UTC).isoformat()


def today() -> str:
    """The current UTC date as ``YYYY-MM-DD``.

    Usage is metered per UTC day rather than per local day so that a fleet
    spanning time zones is counted once, on one calendar, and a customer and
    the vendor reading the same invoice see the same days.
    """
    return datetime.now(UTC).date().isoformat()
