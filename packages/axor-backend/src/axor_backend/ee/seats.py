"""A workspace's limit on developer seats, and who holds the seats this month.

A paid plan bills each developer whose coding agents reported in a month. An
admin can cap that: the first ``cap`` developers to report in the month hold
the seats; one past the cap is still protected — the hooks deny what they must,
whatever the plan — but the plane neither shows nor operates their sessions,
and they are not billed. The limit can be raised at any time; it cannot be set
below the seats this month already used (that would unbill work already done),
and it binds from the month it was set.
"""
from __future__ import annotations

from typing import Any

from axor_backend.licensing import billing_month

SEAT_CAP_SETTING = "coding_seat_cap"
MAX_CAP = 10_000


async def seat_cap(store: Any, month: str | None = None) -> int | None:  # noqa: ANN401
    """The cap binding in ``month`` (YYYY-MM), or None. A cap set in a later
    month does not reach back into one already billed."""
    held = await store.get_setting(SEAT_CAP_SETTING)
    if not isinstance(held, dict) or not isinstance(held.get("cap"), int):
        return None
    if month is not None and str(held.get("since") or "") > month:
        return None
    return int(held["cap"])


async def month_seats(store: Any, month: str, tier: str) -> dict[str, Any]:  # noqa: ANN401
    """Who reported in ``month``, in the order they took seats, and which of
    them are past the cap. The free plan has its own rule (entitlements)."""
    start, end = billing_month(f"{month}-01")
    usage = await store.developer_usage(start, end)
    ordered = sorted(usage["developers"],
                     key=lambda d: (d.get("first_day", ""), d["developer"]))
    cap = await seat_cap(store, month) if tier != "community" else None
    seated = ordered if cap is None else ordered[:cap]
    over = [] if cap is None else ordered[cap:]
    return {"cap": cap, "seated": seated, "over": over,
            "developers": [{**d, "seated": d in seated} for d in ordered]}
