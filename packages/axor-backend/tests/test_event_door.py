"""What ingest accepts, and who is told when it does not.

Two ways the door was open, and they failed in opposite directions.

A batch carrying a bare string, or an event missing `seq` or `kind`, reached the
INSERT and came back as `500 {"error": "internal"}` — the caller's mistake
reported as ours, with nothing in the response they could act on.

A line whose `schema_version` named a major the kernel cannot read was stored
with `202`, and after that EVERY read of the run answered 422 forever — replay,
containment, provenance, the coverage panel, the corpus — with no route that can
remove the lines. The 4xx landed on the operator, at a time nobody could fix it.
`traces.py` exists to turn that into an honest 422, and it still does for data
already stored; what it cannot do is stop it arriving.

The oracle is the kernel's own reader, never a schema check living in the
platform (rule 0). A line with NO `schema_version` is plane telemetry — a proxy
heartbeat — and the kernel is not asked about it.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_core.kernel.events import SCHEMA_VERSION

KERNEL_LINE = {
    "schema_version": SCHEMA_VERSION, "seq": 0, "node_id": "n0",
    "kind": "tool_call", "ts": "t", "payload": {"tool": "x", "args": {}},
}
HEARTBEAT = {"seq": 0, "node_id": "n0", "kind": "heartbeat", "ts": "t"}


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={}, allow_unsigned=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        yield c


class TestAMalformedEventIsTheCallersAndSaysSo:
    @pytest.mark.parametrize(("what", "event", "says"), [
        ("a bare string where an object goes", "hello", "must be an object"),
        ("a number where an object goes", 7, "must be an object"),
        ("no seq", {"kind": "heartbeat"}, "has no `seq`"),
        ("a seq that is not a number", {"kind": "heartbeat", "seq": "x"},
         "must be an integer"),
        ("no kind", {"seq": 0}, "has no `kind`"),
        ("an empty kind", {"seq": 0, "kind": ""}, "has no `kind`"),
        ("a node_id that is not a string", {"seq": 0, "kind": "heartbeat",
                                            "node_id": {"a": 1}},
         "node_id must be a string"),
    ])
    async def test_it_is_a_422_naming_the_index_and_the_field(
        self, client: httpx.AsyncClient, what: str, event: object, says: str,
    ) -> None:
        r = await client.post("/v1/ingest/r", json={"events": [event]})
        assert r.status_code == 422, what
        assert "events[0]" in r.json()["detail"], what
        assert says in r.json()["detail"], what

    async def test_the_index_is_the_offending_one_not_the_first(
        self, client: httpx.AsyncClient,
    ) -> None:
        """A 10 000-line batch with one bad line is unfindable otherwise."""
        r = await client.post("/v1/ingest/r", json={
            "events": [HEARTBEAT, HEARTBEAT | {"seq": 1}, {"seq": 2}],
        })
        assert r.status_code == 422
        assert "events[2]" in r.json()["detail"]

    async def test_nothing_from_a_refused_batch_is_stored(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The valid lines ahead of the bad one must not land either — a half
        stored batch is a trace with a hole in it, and the client was told no."""
        await client.post("/v1/ingest/r", json={"events": [HEARTBEAT, {"seq": 1}]})
        assert (await client.get("/v1/runs/r/events")).json() == []


class TestALineTheKernelCannotReadNeverEntersTheLog:
    @pytest.mark.parametrize(("what", "event"), [
        ("a major the kernel does not know", KERNEL_LINE | {"schema_version": "9.9"}),
        ("a schema_version that is a number", KERNEL_LINE | {"schema_version": 1}),
        ("a kind outside EventKind", KERNEL_LINE | {"kind": "value_created"}),
        ("a verdict outside Verdict", KERNEL_LINE | {"verdict": "maybe"}),
    ])
    async def test_it_is_refused_at_the_door(
        self, client: httpx.AsyncClient, what: str, event: dict,
    ) -> None:
        r = await client.post("/v1/ingest/r", json={"events": [event]})
        assert r.status_code == 422, f"{what}: {r.text}"
        assert "the kernel cannot read it" in r.json()["detail"], what

    async def test_the_run_is_not_poisoned(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The consequence the refusal prevents: one accepted line used to make
        every read of the run 422 for good, with no way to delete it."""
        await client.post("/v1/ingest/r", json={"events": [KERNEL_LINE]})
        await client.post("/v1/ingest/r", json={
            "events": [KERNEL_LINE | {"seq": 1, "schema_version": "9.9"}],
        })
        assert (await client.get("/v1/replay/r")).status_code == 200

    async def test_plane_telemetry_is_not_held_to_the_kernel_schema(
        self, client: httpx.AsyncClient,
    ) -> None:
        """A heartbeat carries no `schema_version` on purpose; asking the kernel
        about it would shut every proxy out of its own keepalive run."""
        r = await client.post("/v1/ingest/r", json={"events": [HEARTBEAT]})
        assert r.status_code == 202
        assert r.json()["stored"] == 1

    async def test_a_good_kernel_line_still_goes_in(
        self, client: httpx.AsyncClient,
    ) -> None:
        r = await client.post("/v1/ingest/r", json={"events": [KERNEL_LINE]})
        assert r.status_code == 202
        assert (await client.get("/v1/replay/r")).status_code == 200

    async def test_the_plane_telemetry_route_has_the_same_door(
        self, client: httpx.AsyncClient,
    ) -> None:
        """`/v1/plane/{node}/telemetry` ingests too, through the same check —
        two doors with one of them open is one open door."""
        r = await client.post("/v1/plane/n0/telemetry", json={
            "run_id": "keepalive", "events": [KERNEL_LINE | {"schema_version": "9.9"}],
        })
        assert r.status_code == 422
        assert "the kernel cannot read it" in r.json()["detail"]


class TestARunHasACeilingToo:
    """`MAX_EVENTS_PER_BATCH` bounds one request. It never bounded the run, and
    `limits.py`'s own docstring names the path it left open: "Replay reads a
    whole run back the same way." Twenty legal batches of 10 000 made a trace
    that `GET /v1/replay/{run}` answered 200 for after 9 s, with a 71 MB body and
    545 MiB resident — on a route that needs only `read` scope.

    Enforced on the write. A ceiling on the read would make an already-recorded
    trace permanently unreadable, and an audit log that cannot be read is worse
    than one that is slow.
    """

    @staticmethod
    def _batch(start: int, n: int) -> list[dict]:
        return [KERNEL_LINE | {"seq": start + i} for i in range(n)]

    async def test_the_batch_that_would_cross_it_is_refused(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("axor_backend.storage.MAX_EVENTS_PER_RUN", 25)
        first = await client.post("/v1/ingest/r", json={"events": self._batch(0, 20)})
        assert first.status_code == 202
        second = await client.post("/v1/ingest/r", json={"events": self._batch(20, 10)})
        assert second.status_code == 413
        detail = second.json()["error"]
        assert "AXOR_MAX_EVENTS_PER_RUN" in detail
        assert "new run id" in detail  # the remedy, named

    async def test_what_was_recorded_stays_readable(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The whole reason the ceiling is on the write and not the read."""
        monkeypatch.setattr("axor_backend.storage.MAX_EVENTS_PER_RUN", 25)
        await client.post("/v1/ingest/r", json={"events": self._batch(0, 20)})
        await client.post("/v1/ingest/r", json={"events": self._batch(20, 10)})
        assert (await client.get("/v1/replay/r")).status_code == 200
        assert len((await client.get("/v1/runs/r/events")).json()) == 20

    async def test_a_new_run_id_is_the_remedy_and_it_works(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("axor_backend.storage.MAX_EVENTS_PER_RUN", 25)
        await client.post("/v1/ingest/r", json={"events": self._batch(0, 20)})
        assert (await client.post("/v1/ingest/r-2", json={
            "events": self._batch(0, 20),
        })).status_code == 202

    async def test_nothing_from_the_refused_batch_is_stored(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Partly accepting it would put the run over the ceiling anyway, and
        leave the client thinking none of it landed."""
        monkeypatch.setattr("axor_backend.storage.MAX_EVENTS_PER_RUN", 25)
        await client.post("/v1/ingest/r", json={"events": self._batch(0, 20)})
        await client.post("/v1/ingest/r", json={"events": self._batch(20, 10)})
        assert len((await client.get("/v1/runs/r/events")).json()) == 20

    async def test_the_plane_telemetry_route_has_the_same_ceiling(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("axor_backend.storage.MAX_EVENTS_PER_RUN", 25)
        await client.post("/v1/plane/n0/telemetry", json={
            "run_id": "keep", "events": self._batch(0, 20),
        })
        r = await client.post("/v1/plane/n0/telemetry", json={
            "run_id": "keep", "events": self._batch(20, 10),
        })
        assert r.status_code == 413
