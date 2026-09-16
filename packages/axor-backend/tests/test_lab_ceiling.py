"""The Lab handoff writes into the events table, so the events ceiling applies.

`MAX_EVENTS_PER_RUN` states its own reason: "every read of a run loads it whole,
so the ceiling is on the run, not the request — continue under a new run id."
`ingest_events` counts and refuses. `add_lab_trace_events`, writing to the SAME
table, counted nothing:

    through the ingest door:
       200 events                    -> 202
       200 more                      -> 202

    through the Lab handoff (add_lab_trace_events), same table:
       250050 events in one call -> stored 250050 in 137.1s
       the run now holds             250050 events (PAST the ceiling)

    and what reading it costs:
       GET /v1/replay/lab:huge       -> 200 in 12.2s, 82 MB

`MAX_PINS_PER_PACKAGE` bounds how MANY pins a package carries; nothing bounded
how many events each carried trace holds. A ceiling one door enforces and the
door beside it does not is not a ceiling.

It is enforced in two places on purpose. The store is where the invariant lives,
so no caller can go around it; the package validator refuses the same thing
earlier, so an oversized package is rejected with every reason listed at once —
the contract this router states — instead of a deploy that writes some pins and
then raises on one.
"""
from __future__ import annotations

import json
import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.errors import RunTooLarge
from axor_backend.lab_import import validate_cp_deploy
from axor_backend.limits import MAX_EVENTS_PER_RUN
from axor_backend.storage import Store, init_db, make_engine
from axor_backend.tenancy import PUBLIC_ORG, set_current_org
from axor_core.kernel.events import SCHEMA_VERSION

TOKEN = "t"
H = {"Authorization": f"Bearer {TOKEN}"}
FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "lab-cp-deploy.json"


def _line(seq: int) -> dict:
    return {"schema_version": SCHEMA_VERSION, "seq": seq, "node_id": "root",
            "kind": "tool_call", "ts": f"seq:{seq}", "causal_root": None,
            "gate": None, "verdict": "pass", "payload": {"tool": "t"}}


@pytest.fixture
async def store(tmp_path: pathlib.Path) -> Store:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/l.db")
    await init_db(engine)
    set_current_org(PUBLIC_ORG)
    return Store(engine)


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/api.db",
        operator_keys={}, allow_unsigned=True, api_token=TOKEN,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test",
        timeout=120,
    ) as c, app.router.lifespan_context(app):
        yield c


class TestTheStoreHoldsTheCeiling:
    async def test_a_lab_trace_cannot_exceed_it(self, store: Store) -> None:
        with pytest.raises(RunTooLarge) as exc:
            await store.add_lab_trace_events(
                "lab:huge", [_line(i) for i in range(MAX_EVENTS_PER_RUN + 1)])
        assert "per-run ceiling" in str(exc.value)
        assert await store.run_events("lab:huge") == []

    async def test_it_counts_what_the_run_already_holds(
        self, store: Store, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two handoffs that each fit cannot add up to a run that does not —
        the same property `ingest_events` has across batches."""
        monkeypatch.setattr(
            "axor_backend.storage.MAX_EVENTS_PER_RUN", 10)
        assert await store.add_lab_trace_events(
            "lab:x", [_line(i) for i in range(6)]) == 6
        with pytest.raises(RunTooLarge) as exc:
            await store.add_lab_trace_events(
                "lab:x", [_line(i) for i in range(100, 106)])
        assert "would add 6 events to the 6 it holds" in str(exc.value)
        assert len(await store.run_events("lab:x")) == 6

    async def test_a_re_upload_of_the_same_trace_still_fits(
        self, store: Store, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Re-asserting is idempotent per (run_id, node_id, seq), so a package
        uploaded twice must not be refused the second time for being too big."""
        monkeypatch.setattr("axor_backend.storage.MAX_EVENTS_PER_RUN", 10)
        lines = [_line(i) for i in range(9)]
        assert await store.add_lab_trace_events("lab:y", lines) == 9
        assert await store.add_lab_trace_events("lab:y", lines) == 0
        assert len(await store.run_events("lab:y")) == 9


class TestThePackageIsRefusedBeforeAnythingIsWritten:
    def test_an_oversized_carried_trace_is_a_reason(self) -> None:
        package = json.loads(FIXTURE.read_text())
        trace_id, body = next(iter(package["regression_traces"].items()))
        body["events"] = [_line(i) for i in range(MAX_EVENTS_PER_RUN + 1)]
        reasons = validate_cp_deploy(package)
        assert any(
            f"regression_traces[{trace_id!r}]" in r and "per-run ceiling" in r
            for r in reasons
        ), reasons

    def test_the_shipped_package_is_still_accepted(self) -> None:
        assert validate_cp_deploy(json.loads(FIXTURE.read_text())) == []

    async def test_the_route_rejects_it_and_stores_nothing(
        self, client: httpx.AsyncClient,
    ) -> None:
        package = json.loads(FIXTURE.read_text())
        body = next(iter(package["regression_traces"].values()))
        body["events"] = [_line(i) for i in range(MAX_EVENTS_PER_RUN + 1)]
        r = await client.post("/v1/lab/deploy", json=package, headers=H)
        assert r.status_code == 422
        assert any("per-run ceiling" in reason
                   for reason in r.json()["detail"]["reasons"])
        assert (await client.get("/v1/lab/deploys", headers=H)).json() == []


class TestTheConversionIsNotOnTheEventLoop:
    @pytest.mark.parametrize(("method", "path", "expected"), [
        ("GET", "/v1/runs/r/lab-package", {"build_incident_package"}),
        ("POST", "/v1/lab/deploy", {"validate_cp_deploy", "deploy_plans"}),
    ])
    async def test_each_route_hands_its_work_off(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
        method: str, path: str, expected: set[str],
    ) -> None:
        """A 40 000-event run held the loop for 1181 ms in `lab-package` — for a
        conversion that then answered 422, so the deployment paid in full for a
        refusal."""
        from axor_backend import offload

        handed: list[str] = []
        real = offload.asyncio.to_thread

        async def counting(  # noqa: ANN202
            fn, /, *args: object, **kwargs: object,  # noqa: ANN001
        ):
            handed.append(getattr(fn, "__name__", repr(fn)))
            return await real(fn, *args, **kwargs)

        monkeypatch.setattr(offload.asyncio, "to_thread", counting)
        await client.post("/v1/ingest/r", headers=H,
                          json={"events": [_line(i) for i in range(3)]})
        await client.request(
            method, path, headers=H,
            json=json.loads(FIXTURE.read_text()) if method == "POST" else None)
        missing = expected - set(handed)
        assert not missing, (
            f"{method} {path} called {sorted(missing)} on the event loop "
            f"(handed off: {sorted(set(handed))})"
        )


class TestTheIngestDoorCountsTheSameWay:
    """The ceiling used to be measured against the batch as SENT, on both doors.

    A re-sent batch adds nothing — ingest is idempotent on (node_id, seq) — but
    it was counted in full, so near the ceiling a retry could never succeed and
    the client was told to start a new run id for events already stored under
    this one. Counting after the duplicates are dropped is what makes the
    ceiling a statement about the run rather than about the request.
    """

    async def test_a_re_sent_batch_does_not_count_against_it(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("axor_backend.storage.MAX_EVENTS_PER_RUN", 10)
        batch = {"events": [_line(i) for i in range(9)]}
        assert (await client.post("/v1/ingest/r", json=batch,
                                  headers=H)).status_code == 202
        # the same nine events again: zero new, so still within the ceiling
        assert (await client.post("/v1/ingest/r", json=batch,
                                  headers=H)).status_code == 202
        assert len((await client.get("/v1/runs/r/events", headers=H)).json()) == 9

    async def test_a_partly_duplicate_batch_counts_only_what_is_new(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The case a wholly-duplicate batch cannot show.

        A batch with no new events returns before the ceiling is even measured,
        so it passes whichever way the count is written. A retry that carries
        the nine already stored plus one new event is the one that separates
        them: one new event fits under a ceiling of ten, the ten lines as sent
        do not.
        """
        monkeypatch.setattr("axor_backend.storage.MAX_EVENTS_PER_RUN", 10)
        await client.post("/v1/ingest/r",
                          json={"events": [_line(i) for i in range(9)]}, headers=H)
        r = await client.post("/v1/ingest/r",
                              json={"events": [_line(i) for i in range(10)]},
                              headers=H)
        assert r.status_code == 202, r.text
        assert len((await client.get("/v1/runs/r/events", headers=H)).json()) == 10

    async def test_the_ceiling_still_refuses_genuinely_new_events(
        self, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("axor_backend.storage.MAX_EVENTS_PER_RUN", 10)
        await client.post("/v1/ingest/r",
                          json={"events": [_line(i) for i in range(9)]}, headers=H)
        r = await client.post(
            "/v1/ingest/r",
            json={"events": [_line(i) for i in range(100, 105)]}, headers=H)
        assert r.status_code == 413
        assert "per-run ceiling" in r.text
        assert len((await client.get("/v1/runs/r/events", headers=H)).json()) == 9
