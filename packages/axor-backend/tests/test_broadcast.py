"""The in-process SSE bus: what it holds, and what it does to a slow reader.

Single-instance by design means this dict is the only fan-out the deployment
has, and the process is expected to run for weeks. Both properties below are
about that: it must not grow on its own, and a subscriber it gives up on must
find out.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib

import pytest
from axor_backend.broadcast import OVERFLOW, Broadcast, messages
from axor_backend.tenancy import topic


class TestTheBusHoldsOnlyWhatIsWatched:
    """`self._subs[topic]` on a defaultdict CREATED the entry, and topics carry
    caller-chosen ids — so every ingest with nobody watching left a permanent
    empty set behind, in a process that never restarts on its own."""

    def test_publishing_into_the_void_holds_nothing(self) -> None:
        bus = Broadcast()
        for i in range(10_000):
            bus.publish(topic("run", f"run_{i}"), {"type": "event", "id": i})
        assert bus.topics() == 0

    def test_the_last_unsubscribe_releases_the_topic(self) -> None:
        bus = Broadcast()
        t = topic("run", "r0")
        first, second = bus.subscribe(t), bus.subscribe(t)
        assert bus.topics() == 1
        bus.unsubscribe(t, first)
        assert bus.topics() == 1  # the other reader is still there
        bus.unsubscribe(t, second)
        assert bus.topics() == 0

    def test_unsubscribing_twice_is_not_an_error(self) -> None:
        bus = Broadcast()
        t = topic("run", "r0")
        q = bus.subscribe(t)
        bus.unsubscribe(t, q)
        bus.unsubscribe(t, q)  # the topic is gone by now
        assert bus.topics() == 0


class TestASlowReaderIsToldRatherThanAbandoned:
    """It used to be discarded from the set while parked on `queue.get()`: its
    `finally: unsubscribe` never ran, the HTTP response stayed open, and
    EventSource does not reconnect a stream that has not closed. The comment
    promising it would "re-sync via snapshot/replay" described something that
    could not happen."""

    def test_the_marker_replaces_the_message_that_did_not_fit(self) -> None:
        bus = Broadcast()
        t = topic("run", "r1")
        q = bus.subscribe(t)
        for i in range(1000):  # exactly maxsize
            bus.publish(t, {"type": "event", "id": i})
        assert bus.topics() == 1
        bus.publish(t, {"type": "event", "id": 1000})  # one too many

        assert bus.topics() == 0  # dropped, and the topic released with it
        drained = []
        while not q.empty():
            drained.append(q.get_nowait())
        assert drained[-1]["type"] == OVERFLOW
        assert drained[-1]["topic"] == t

    def test_it_says_so_in_the_log(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        bus = Broadcast()
        t = topic("run", "r1")
        bus.subscribe(t)
        for i in range(1001):
            bus.publish(t, {"type": "event", "id": i})
        with caplog.at_level(logging.WARNING, logger="axor.backend.broadcast"):
            bus.subscribe(t)
            for i in range(1001):
                bus.publish(t, {"type": "event", "id": i})
        assert any("fell behind" in r.getMessage() for r in caplog.records)


class TestTheSharedLoopEndsOnTheMarker:
    """Both SSE routes iterate `messages(queue)` rather than pulling from the
    queue themselves. One loop, so the marker cannot be forgotten by the next
    stream, and it is testable without a live HTTP response — which this suite
    cannot interleave with a publish anyway."""

    async def test_it_yields_until_the_bus_gives_up_on_the_reader(self) -> None:
        bus = Broadcast()
        t = topic("run", "r1")
        q = bus.subscribe(t)
        for i in range(1000):
            bus.publish(t, {"type": "event", "id": i})
        bus.publish(t, {"type": "event", "id": 1000})  # overflows → marker

        # Bounded: without the marker check `messages` never returns, and an
        # unbounded comprehension would hang the suite instead of failing it.
        async with asyncio.timeout(5):
            seen = [message async for message in messages(q)]
        assert len(seen) == 999            # the dropped one made room
        assert all(m["type"] == "event" for m in seen)

    async def test_it_keeps_yielding_while_the_reader_keeps_up(self) -> None:
        bus = Broadcast()
        t = topic("run", "r1")
        q = bus.subscribe(t)
        bus.publish(t, {"type": "event", "id": 1})
        bus.publish(t, {"type": "event", "id": 2})

        stream = messages(q)
        assert (await anext(stream))["id"] == 1
        assert (await anext(stream))["id"] == 2
        with pytest.raises(TimeoutError):   # still open, waiting for more
            await asyncio.wait_for(anext(stream), 0.05)

    def test_both_sse_routes_use_it(self) -> None:
        """The guard against the next stream rolling its own loop again."""
        src = pathlib.Path(__file__).resolve().parents[1] / "src" / "axor_backend"
        streams = [
            path for path in src.rglob("*.py")
            if "EventSourceResponse" in path.read_text("utf-8")
        ]
        assert {path.name for path in streams} == {"plane.py", "runs.py"}
        for path in streams:
            body = path.read_text("utf-8")
            assert "bus_messages(queue)" in body, (
                f"{path.name} feeds an SSE response from the bus without the "
                f"shared loop — a reader the bus drops would park on "
                f"queue.get() forever and its response would never close"
            )
