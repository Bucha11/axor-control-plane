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
