"""The sign-up funnel, measured first-party and anonymously.

Steps, in order (FUNNEL):
  visit → guest_started → first_value (demo_fleet_loaded | lie_caught) →
  signup_prompt_shown → signup_completed → guest_claimed → agent_connected

The browser posts the steps it sees with a random per-tab id
(`POST /v1/funnel`, open, rate-limited, whitelisted names only, a short
detail such as the feature that prompted a sign-up). `agent_connected` is
recorded by the plane itself, once per (org, node), when a real tenant's
node first reports — the step that means the product is in use.

Nothing personal is stored: no IP, no user id, no email, no org. The report
(`GET /v1/funnel`) is the operator's (master token) and counts distinct tab
ids per step over a window.
"""
from __future__ import annotations

import re
import time
from collections import deque

EVENTS = (
    "visit", "guest_started", "demo_fleet_loaded", "lie_caught",
    "signup_prompt_shown", "signup_completed", "guest_claimed", "agent_connected",
    "control_used",
)
CLIENT_EVENTS = frozenset(EVENTS) - {"agent_connected", "control_used"}
# which session controls people use (detail = the control, never who or where):
# what is worth building on, measured instead of guessed
CONTROLS = ("pause", "resume", "stop", "budget", "replan", "inject", "cascade_stop",
            "tree_pause", "tree_resume", "tree_stop", "say", "brake_on", "brake_off",
            "pair_open", "pair_note", "ask_to_pair", "pair_let_in")
_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_DETAIL = re.compile(r"^[a-z0-9 _.-]{0,64}$")
PER_IP_PER_MIN = 60

# the report's rows: (label, events counted as reaching that step)
FUNNEL: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("visited", ("visit",)),
    ("worked as a guest", ("guest_started",)),
    ("saw it work (demo fleet or a caught lie)", ("demo_fleet_loaded", "lie_caught")),
    ("was asked to sign up", ("signup_prompt_shown",)),
    ("signed up", ("signup_completed",)),
    ("kept their guest work", ("guest_claimed",)),
)


async def used(store: object, control: str) -> None:
    """One use of a session control, anonymously (funnel_events)."""
    if control not in CONTROLS:
        return
    from datetime import UTC, datetime

    try:
        await store.add_funnel_event(datetime.now(UTC).isoformat(), "server",  # type: ignore[attr-defined]
                                     "control_used", control)
    except Exception:  # noqa: BLE001 - a count never breaks a control
        pass


def valid(event: object, session_id: object, detail: object) -> bool:
    return (event in CLIENT_EVENTS and isinstance(session_id, str) and bool(_ID.match(session_id))
            and isinstance(detail, str) and bool(_DETAIL.match(detail)))


class RateLimit:
    def __init__(self, per_min: int = PER_IP_PER_MIN) -> None:
        self._per_min = per_min
        self._hits: dict[str, deque[float]] = {}

    def allow(self, address: str) -> bool:
        now = time.monotonic()
        hits = self._hits.setdefault(address, deque())
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self._per_min:
            return False
        hits.append(now)
        if len(self._hits) > 10_000:
            self._hits = {address: hits}
        return True
