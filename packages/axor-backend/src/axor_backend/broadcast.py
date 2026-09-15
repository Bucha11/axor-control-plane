"""In-process fan-out for SSE subscribers.

Single-instance by design (architecture: process-local SSE buffer + sticky
sessions; LISTEN/NOTIFY replaces this only when one instance stops coping).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger("axor.backend.broadcast")

# Put into a subscriber's queue in place of the message that would not fit. The
# stream generators recognise it and END the response: a dropped subscriber that
# keeps its connection open is a stream that looks healthy and will never
# deliver again (see `publish`).
OVERFLOW = "__overflow__"


async def messages(
    queue: asyncio.Queue[dict[str, Any]],
) -> AsyncIterator[dict[str, Any]]:
    """Yield a subscriber's messages, and STOP when the bus has dropped it.

    Both SSE routes ran their own ``while True: await queue.get()`` and neither
    checked for the drop, so a reader the bus gave up on parked here forever:
    its ``finally: unsubscribe`` never ran, its HTTP response never closed, and
    EventSource does not reconnect a stream that is still open. One loop, in one
    place, so the next stream cannot forget — ending the generator closes the
    response, the client reconnects with Last-Event-ID and replays the gap.
    """
    while True:
        message = await queue.get()
        if message.get("type") == OVERFLOW:
            return
        yield message


class Broadcast:
    def __init__(self) -> None:
        # A plain dict, not a defaultdict. Reading `self._subs[topic]` in
        # `publish` CREATED the entry, and topics carry caller-chosen ids
        # (`run:{run_id}`, `plane:{node_id}`), so every ingest with nobody
        # watching left a permanent empty set behind — 50 000 of them measured
        # at ~15 MiB, in a process that is single by design and never restarts
        # on its own.
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}

    def subscribe(self, topic: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._subs.setdefault(topic, set()).add(q)
        return q

    def unsubscribe(self, topic: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        subs = self._subs.get(topic)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            del self._subs[topic]  # an empty set is a topic nobody is watching

    def topics(self) -> int:
        """How many topics are currently held — for the leak this used to have."""
        return len(self._subs)

    def publish(self, topic: str, message: dict[str, Any]) -> None:
        subs = self._subs.get(topic)
        if not subs:
            return
        for q in list(subs):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # A slow consumer used to be dropped from the set and left
                # parked on `queue.get()` forever: its `finally: unsubscribe`
                # never ran, the HTTP response stayed open, and EventSource has
                # no reason to reconnect a stream that has not closed — so the
                # comment promising it would "re-sync via snapshot/replay" was
                # describing something that could not happen.
                #
                # Make room and hand it the overflow marker instead. The
                # generator ends the response, the client reconnects with
                # Last-Event-ID, and the replay covers the gap including the one
                # message discarded here.
                try:
                    q.get_nowait()                       # make room
                    q.put_nowait({"type": OVERFLOW, "topic": topic})
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    pass
                subs.discard(q)
                log.warning(
                    "subscriber on %r fell behind (1000 queued) — closing its "
                    "stream so it reconnects and replays", topic,
                )
        if not subs:
            del self._subs[topic]
