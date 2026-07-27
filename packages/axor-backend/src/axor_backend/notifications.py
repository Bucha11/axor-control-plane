"""Notifications — loud elsewhere (spec section 16).

Quiet-until-wrong assumes someone is looking at the screen; the on-call
persona is not. The pairing rule: the UI is quiet, the notification channel is
where wrong gets loud. We emit and route — never a pager.

- Channel: webhook (JSON POST). Slack/Discord/PagerDuty are webhook consumers.
- Triggers (v1): degradation transition upward, Sentinel heat crossing a
  threshold, a run completing with >=1 EvidenceCase, a node stale, a corpus
  run that regressed (regression_failed).
- Routing (EE): subscriptions carry a node glob + channel label; the free
  shape is one global webhook (pattern "*").
- Failure honesty: at-least-once with retries and a dead-letter log visible in
  settings — a notification system that fails silently is worse than none.
  Dead letters persist (capped, migration 0002): a restart must not erase the
  evidence that deliveries were lost.
- Source: the plane event feed the backend already has; a subscriber with an
  HTTP sink and per-trigger debounce, no new instrumentation.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Any

TRIGGERS = frozenset({
    "level_transition_up", "heat_threshold", "evidence_run", "node_stale",
    "regression_failed", "behavioral_drift",
})


@dataclass
class Subscription:
    url: str
    triggers: frozenset[str]
    debounce_seconds: float = 0.0
    # Routing (EE): a named channel + node glob. "*" = everything — the free
    # single-webhook shape. Matching is fnmatch on the emitting node_id.
    label: str = ""
    node_pattern: str = "*"
    # last fire time per (trigger, node) — debounce key. Trace-free: we stamp
    # from a monotonic counter passed in, so tests stay deterministic.
    _last: dict[tuple[str, str], float] = field(default_factory=dict)


@dataclass
class DeadLetter:
    url: str
    payload: dict[str, Any]
    error: str
    attempts: int


class Notifier:
    """One notifier per backend; subscriptions are per-connection config."""

    def __init__(
        self,
        post: Any = None,  # noqa: ANN401 - injected async POST(url, json) -> status
        max_attempts: int = 4,
        dead_letter_cap: int = 500,
        dead_sink: Any = None,  # noqa: ANN401 - async callable(DeadLetter) that persists it
    ) -> None:
        self._subs: list[Subscription] = []
        self._post = post or _default_post
        self._max_attempts = max_attempts
        self._dead: deque[DeadLetter] = deque(maxlen=dead_letter_cap)
        self._dead_sink = dead_sink
        self._clock = 0.0  # logical clock; debounce is in these units

    def subscribe(
        self, url: str, triggers: list[str], debounce_seconds: float = 0.0,
        label: str = "", node_pattern: str = "*",
    ) -> None:
        bad = set(triggers) - TRIGGERS
        if bad:
            raise ValueError(f"unknown triggers: {sorted(bad)}")
        self._subs.append(Subscription(
            url, frozenset(triggers), debounce_seconds,
            label=label, node_pattern=node_pattern or "*",
        ))

    def tick(self, dt: float = 1.0) -> None:
        self._clock += dt

    @property
    def dead_letters(self) -> list[DeadLetter]:
        return list(self._dead)

    async def emit(
        self, trigger: str, node_id: str, payload: dict[str, Any]
    ) -> int:
        """Fan a triggering event out to matching subscriptions. Returns the
        number of subscriptions delivered to (after debounce)."""
        if trigger not in TRIGGERS:
            raise ValueError(f"unknown trigger {trigger!r}")
        delivered = 0
        body = {"trigger": trigger, "node_id": node_id, **payload}
        for sub in self._subs:
            if trigger not in sub.triggers:
                continue
            if not fnmatch(node_id, sub.node_pattern):
                continue
            key = (trigger, node_id)
            if sub.debounce_seconds > 0:
                last = sub._last.get(key)
                if last is not None and (self._clock - last) < sub.debounce_seconds:
                    continue
                sub._last[key] = self._clock
            await self._deliver(sub.url, body)
            delivered += 1
        return delivered

    async def _deliver(self, url: str, body: dict[str, Any]) -> None:
        backoff = 0.05
        last_error = ""
        for attempt in range(1, self._max_attempts + 1):
            try:
                status = await self._post(url, body)
                if 200 <= status < 300:
                    return
                last_error = f"status {status}"
            except Exception as exc:  # noqa: BLE001 - honest dead-letter
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self._max_attempts:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 2.0)
        letter = DeadLetter(url=url, payload=body, error=last_error,
                            attempts=self._max_attempts)
        self._dead.append(letter)
        if self._dead_sink is not None:
            # Persistence must never make emit() itself fail — a broken DB on
            # top of a broken webhook still leaves the in-memory record.
            try:
                await self._dead_sink(letter)
            except Exception:  # noqa: BLE001 - deliberately non-fatal
                logging.getLogger("axor.notifications").exception(
                    "dead-letter persist failed"
                )


async def _default_post(url: str, body: dict[str, Any]) -> int:
    import httpx

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            url, content=json.dumps(body).encode(),
            headers={"content-type": "application/json"},
        )
        return resp.status_code
