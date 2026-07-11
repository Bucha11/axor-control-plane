"""In-process fan-out for SSE subscribers.

Single-instance by design (architecture: process-local SSE buffer + sticky
sessions; LISTEN/NOTIFY replaces this only when one instance stops coping).
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class Broadcast:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subs[topic].add(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs[topic].discard(q)

    def publish(self, topic: str, message: dict[str, Any]) -> None:
        for q in list(self._subs[topic]):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # Slow consumer: drop it; SSE clients re-sync via snapshot/replay.
                self._subs[topic].discard(q)
