"""The node-stale monitor: paging on the ABSENCE of a signal (spec §16).

Every other trigger fires on an event. This one fires on silence, which makes
its edge detection the whole mechanism: page on the transition, once, and again
only after the node has spoken and gone quiet anew. The edge is a column on the
node's row precisely so that the answer to "have we already paged for this
silence?" outlives the process asking.
"""
from __future__ import annotations

import logging
import pathlib
from datetime import UTC, datetime, timedelta

import pytest
from axor_backend.app import create_app
from axor_backend.broadcast import Broadcast
from axor_backend.monitor import spawn_stale_monitor, stale_sweep
from axor_backend.notifications import Notifier
from axor_backend.storage import Store


@pytest.fixture
async def store(tmp_path: pathlib.Path) -> Store:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/monitor.db",
        operator_keys={}, allow_unsigned=True,
    )
    async with app.router.lifespan_context(app):  # runs the migrations
        yield app.state.store


@pytest.fixture
def pages() -> list[dict]:
    return []


@pytest.fixture
def notifier(pages: list[dict]) -> Notifier:
    async def capture(url: str, body: dict) -> int:
        pages.append(body)
        return 200

    n = Notifier(post=capture)
    n.subscribe("http://sink.test", ["node_stale"])
    return n


async def _beat(store: Store, node: str, when: datetime) -> None:
    await store.upsert_reported(
        node, applied_version=1, level="NORMAL", budget_remaining=None,
        ts=when.isoformat(),
    )


async def _sweep(store: Store, notifier: Notifier, now: datetime) -> int:
    return await stale_sweep(store, notifier, Broadcast(), 30.0, now)


class TestTheEdgeIsTheNodesRowNotTheProcess:
    async def test_a_restart_is_not_a_heartbeat(
        self, store: Store, notifier: Notifier,
    ) -> None:
        """The flag used to be a set in process memory, and nothing deletes a
        reported row — so every node that had ever gone quiet, decommissioned
        ones included, paged again on the first sweep after every restart. Fifty
        of them meant fifty pages, on every restart, forever."""
        now = datetime.now(UTC)
        long_gone = now - timedelta(days=90)
        for i in range(50):
            await _beat(store, f"decommissioned-{i}", long_gone)

        assert await _sweep(store, notifier, now) == 50
        # Each "restart" is a fresh sweep with nothing remembered in process.
        assert await _sweep(store, notifier, now) == 0
        assert await _sweep(store, notifier, now) == 0

    async def test_a_node_that_went_silent_while_the_backend_was_down_is_paged(
        self, store: Store, notifier: Notifier,
    ) -> None:
        """The other half: not paging twice must not become never paging. A node
        whose silence began during the outage has no flag set, so the first
        sweep after boot is its transition."""
        now = datetime.now(UTC)
        await _beat(store, "n1", now - timedelta(seconds=40))
        assert await _sweep(store, notifier, now) == 1

    async def test_a_heartbeat_re_arms_the_page(
        self, store: Store, notifier: Notifier, pages: list[dict],
    ) -> None:
        now = datetime.now(UTC)
        await _beat(store, "n1", now - timedelta(seconds=5))
        assert await _sweep(store, notifier, now) == 0        # fresh

        await _beat(store, "n1", now - timedelta(seconds=40))
        assert await _sweep(store, notifier, now) == 1        # crossed
        assert await _sweep(store, notifier, now) == 0        # edge holds

        await _beat(store, "n1", now)                         # spoke again
        assert await _sweep(store, notifier, now) == 0
        await _beat(store, "n1", now - timedelta(seconds=40))  # and went quiet
        assert await _sweep(store, notifier, now) == 1

        await notifier.drain()
        assert [p["trigger"] for p in pages] == ["node_stale", "node_stale"]
        assert pages[0]["node_id"] == "n1"

    async def test_the_heartbeat_clears_the_flag_in_its_own_write(
        self, store: Store,
    ) -> None:
        """The re-arm is `upsert_reported`'s doing, so there is no window where
        a heartbeat has landed and the node still counts as reported-stale."""
        now = datetime.now(UTC)
        await _beat(store, "n1", now - timedelta(seconds=40))
        await store.mark_stale_notified("n1", now.isoformat())
        assert (await store.list_reported())[0]["stale_notified"] is not None
        await _beat(store, "n1", now)
        assert (await store.list_reported())[0]["stale_notified"] is None

    async def test_a_settled_node_costs_no_write_at_all(
        self, store: Store, notifier: Notifier,
    ) -> None:
        """The flag is read from the row the sweep already loaded, so a fleet
        that is stale and stays stale is not N writes every ten seconds forever.
        The conditional write is the correctness guard; this is the one that
        keeps the guard from being the cost."""
        now = datetime.now(UTC)
        for i in range(5):
            await _beat(store, f"n{i}", now - timedelta(seconds=40))

        writes = 0
        real = store.mark_stale_notified

        async def counting(node_id: str, ts: str) -> bool:
            nonlocal writes
            writes += 1
            return await real(node_id, ts)

        store.mark_stale_notified = counting  # type: ignore[method-assign]
        assert await _sweep(store, notifier, now) == 5
        assert writes == 5           # one claim each, the first time
        assert await _sweep(store, notifier, now) == 0
        assert writes == 5           # and nothing at all thereafter

    async def test_claiming_the_silence_is_the_write_itself(
        self, store: Store,
    ) -> None:
        """`mark_stale_notified` writes only while the flag is unset, so two
        sweeps racing page once between them, not once each."""
        now = datetime.now(UTC)
        await _beat(store, "n1", now - timedelta(seconds=40))
        assert await store.mark_stale_notified("n1", now.isoformat()) is True
        assert await store.mark_stale_notified("n1", now.isoformat()) is False


class TestAnUnreadableTimestampIsLoud:
    async def test_it_says_the_node_is_not_being_watched(
        self, store: Store, notifier: Notifier,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The sweep's one input is unreadable, so it cannot tell whether the
        node is silent. Saying nothing about that is the exact failure this file
        exists to prevent — it used to `continue` without a word."""
        await _beat(store, "ok", datetime.now(UTC))
        await store.upsert_reported(
            "bad-ts", applied_version=1, level="NORMAL", budget_remaining=None,
            ts="not-a-timestamp",
        )
        with caplog.at_level(logging.WARNING, logger="axor.backend.monitor"):
            assert await _sweep(store, notifier, datetime.now(UTC)) == 0
        lines = [r.getMessage() for r in caplog.records]
        assert any("bad-ts" in line and "NOT being watched" in line
                   for line in lines), lines


class TestTheCadenceEnvVarsAreValidated:
    def test_a_typo_names_the_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`float(os.environ[...])` killed startup with an unlabelled ValueError."""
        app = type("App", (), {"state": type("S", (), {})()})()
        monkeypatch.setenv("AXOR_STALE_AFTER", "thirty")
        with pytest.raises(ValueError, match="AXOR_STALE_AFTER"):
            spawn_stale_monitor(app)
        monkeypatch.setenv("AXOR_STALE_AFTER", "0")
        with pytest.raises(ValueError, match="must be > 0"):
            spawn_stale_monitor(app)
