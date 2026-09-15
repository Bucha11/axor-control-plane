"""A custody log the writer cannot flush, and an attestation that expires.

Both vaults keep an append-only record of privileged actions, and both kept it
as one JSON array under a settings key, trimmed to the newest N on every append.
Eviction was therefore a function of WRITES — and the surface that writes is the
surface the log is about.

    after one real signature: 1 row(s)
       op_alice / k_release / granted=True / 004707090d615d56…
    after 520 refused requests: 500 row(s)
       the signature over 'ship v2.0 to production' is still there: False

Refusals are logged unconditionally (that fix closed key-id probing) and naming
a key id that does not exist costs nothing, so 520 free requests erased a real
signature. The credential log is the same shape: 1019 further dispenses and the
drain that prompted the question is gone, which is the sentence
`vault_dispense`'s docstring makes — "draining a scope now looks like what it
is" — untrue for anything but the most recent thousand.

Eviction is by AGE now (migration 0015): writing rows cannot accelerate the
clock. The disk backstop still exists, because a deployment that keeps history
forever still has a disk, but it lives in the sweep rather than in the append —
counting rows on every insert put the blob's own cost straight back (2.75 ms at
2k rows, 5.12 ms at 14k, against the 5.5 ms the 1000-row blob cost at its cap).
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.storage import Store, init_db, make_engine
from axor_backend.tenancy import PUBLIC_ORG, set_current_org
from axor_backend.vault_signing import SigningCustody, SignRefused

CREDS, SIGNING = "creds-token", "signing-token"
CH = {"x-vault-creds-token": CREDS}
SH = {"x-vault-signing-token": SIGNING}


@pytest.fixture
async def store(tmp_path: pathlib.Path) -> Store:
    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/v.db")
    await init_db(engine)
    set_current_org(PUBLIC_ORG)
    return Store(engine)


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={}, allow_unsigned=True,
        vault_creds_token=CREDS, vault_signing_token=SIGNING,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        c._app = app  # type: ignore[attr-defined]
        yield c


class TestTheSignerCannotFlushTheSigningLog:
    async def test_free_refusals_no_longer_erase_a_real_signature(
        self, store: Store,
    ) -> None:
        vault = SigningCustody(store)
        await vault.create_key("k_release", ["op_alice"])
        await vault.sign("op_alice", "k_release", b"ship v2.0 to production",
                         principal="key_alice")
        for i in range(520):
            with pytest.raises(SignRefused):
                await vault.sign("op_alice", f"k_nope_{i}", b"x",
                                 principal="key_alice")
        audit = await vault.audit(limit=5000)
        assert len(audit) == 521
        granted = [r for r in audit if r["granted"]]
        assert len(granted) == 1
        assert granted[0]["key_id"] == "k_release"

    async def test_refusals_are_still_recorded(self, store: Store) -> None:
        """The hole this cap made free was closed by a fix worth keeping."""
        vault = SigningCustody(store)
        with pytest.raises(SignRefused):
            await vault.sign("mallory", "probing", b"x")
        audit = await vault.audit()
        assert audit[0]["refusal"] == "unknown key"
        assert audit[0]["granted"] is False


class TestTheDispenserCannotFlushTheDispenseLog:
    async def test_further_dispenses_no_longer_erase_the_drain(
        self, store: Store,
    ) -> None:
        from axor_backend.vault_dispense import AUDIT_KIND, log_entry

        def row(i: int) -> dict:
            return log_entry(
                {"node_id": "n1", "tool": "pay", "endpoint": "https://pay",
                 "timestamp": "t", "run_id": f"r{i}"},
                version=1, signed=True, principal="key_node", ts="t",
            )

        await store.add_vault_audit(
            AUDIT_KIND, row(0) | {"run_id": "THE_DRAIN"}, "2026-09-12T00:00:00Z")
        for i in range(1, 1020):
            await store.add_vault_audit(AUDIT_KIND, row(i), "2026-09-12T00:00:01Z")
        log = await store.vault_audit_entries(AUDIT_KIND, limit=5000)
        assert len(log) == 1020
        assert any(r.get("run_id") == "THE_DRAIN" for r in log)


class TestEvictionFollowsTheClockNotTheWriter:
    async def test_the_retention_window_ages_rows_out(
        self, store: Store,
    ) -> None:
        await store.add_vault_audit("signing", {"a": 1}, "2020-01-01T00:00:00Z")
        await store.add_vault_audit("signing", {"a": 2}, "2030-01-01T00:00:00Z")
        assert await store.prune_vault_audit_older_than("2025-01-01T00:00:00Z") == 1
        assert [r["a"] for r in await store.vault_audit_entries("signing")] == [2]

    async def test_the_sweep_itself_ages_the_audit_out(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """Through `prune_once`, not through the store method it calls. A store
        that can age rows out and a sweep that never asks it to is the shape
        this whole file is about."""
        from axor_backend.lifecycle import prune_once

        app = create_app(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/age.db",
            operator_keys={}, allow_unsigned=True, retention_days=30.0,
        )
        async with app.router.lifespan_context(app):
            store = app.state.store
            await store.add_vault_audit("signing", {"a": "ancient"},
                                        "2020-01-01T00:00:00Z")
            await store.add_vault_audit("creds_dispense", {"a": "ancient"},
                                        "2020-01-01T00:00:00Z")
            await store.add_vault_audit(
                "signing", {"a": "recent"}, datetime.now(UTC).isoformat())
            await prune_once(app.state)
            assert [r["a"] for r in await store.vault_audit_entries("signing")] \
                == ["recent"]
            assert await store.vault_audit_entries("creds_dispense") == []

    async def test_the_sweep_itself_applies_the_backstop(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Also through `prune_once`, and with NO window configured — the
        backstop is not retention, and a deployment that keeps its history
        forever still has a disk."""
        from axor_backend import lifecycle

        app = create_app(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/bs.db",
            operator_keys={}, allow_unsigned=True,
        )
        async with app.router.lifespan_context(app):
            store = app.state.store
            assert app.state.config.retention_days is None
            for i in range(12):
                await store.add_vault_audit("signing", {"i": i}, "2026-09-12T00:00:00Z")
            real = store.trim_vault_audit_to

            async def trim(kind: str, backstop: int = 5) -> int:
                return await real(kind, backstop)

            monkeypatch.setattr(store, "trim_vault_audit_to", trim)
            await lifecycle.prune_once(app.state)
            assert [r["i"] for r in await store.vault_audit_entries("signing")] \
                == [11, 10, 9, 8, 7]

    async def test_the_backstop_is_not_retention_and_runs_regardless(
        self, tmp_path: pathlib.Path,
    ) -> None:
        """`prune_once` used to return immediately with no window configured,
        and the loop that calls it was not even started. A deployment that keeps
        its history forever still has a disk — the same lesson the license sweep
        taught, one step further."""
        from axor_backend.lifecycle import prune_once

        app = create_app(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/b.db",
            operator_keys={}, allow_unsigned=True,
        )
        async with app.router.lifespan_context(app):
            assert app.state.config.retention_days is None  # keep forever
            for i in range(12):
                await app.state.store.add_vault_audit(
                    "signing", {"i": i}, "2026-09-12T00:00:00Z")
            await app.state.store.trim_vault_audit_to("signing", backstop=5)
            left = await app.state.store.vault_audit_entries("signing")
            assert [r["i"] for r in left] == [11, 10, 9, 8, 7]
            await prune_once(app.state)  # must not raise with no window set

    async def test_the_loop_starts_even_with_no_window(
        self, tmp_path: pathlib.Path,
    ) -> None:
        import asyncio

        app = create_app(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/l.db",
            operator_keys={}, allow_unsigned=True,
        )
        before = {t.get_coro().__qualname__ for t in asyncio.all_tasks()}
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0)
            running = {
                t.get_coro().__qualname__ for t in asyncio.all_tasks()
                if t.get_coro().__qualname__ not in before
            }
        assert "retention_loop" in running

    async def test_a_trim_below_the_row_count_drops_nothing(
        self, store: Store,
    ) -> None:
        await store.add_vault_audit("signing", {"a": 1}, "t")
        assert await store.trim_vault_audit_to("signing", backstop=100) == 0
        assert len(await store.vault_audit_entries("signing")) == 1


class TestTheLogsStayApart:
    async def test_one_kind_does_not_read_or_trim_the_other(
        self, store: Store,
    ) -> None:
        """One table, two logs — the WALL (spec v2 Ch.5 §3) is about credentials
        and signing keys, not about storage, but the rows must not mix."""
        await store.add_vault_audit("signing", {"which": "signing"}, "t")
        await store.add_vault_audit("creds_dispense", {"which": "creds"}, "t")
        assert [r["which"] for r in await store.vault_audit_entries("signing")] \
            == ["signing"]
        # Trimming one to its last row must leave BOTH rows standing: each kind
        # has exactly one. Counting across kinds would make the busier log evict
        # the quieter one — a credential vault under load erasing the signing
        # audit is the same flush from a different direction.
        await store.trim_vault_audit_to("signing", backstop=1)
        assert len(await store.vault_audit_entries("signing")) == 1
        assert len(await store.vault_audit_entries("creds_dispense")) == 1

    async def test_a_busy_log_does_not_evict_the_quiet_one(
        self, store: Store,
    ) -> None:
        await store.add_vault_audit("signing", {"which": "the signature"}, "t")
        for i in range(20):
            await store.add_vault_audit("creds_dispense", {"i": i}, "t")
        await store.trim_vault_audit_to("creds_dispense", backstop=5)
        assert [r["which"] for r in await store.vault_audit_entries("signing")] \
            == ["the signature"]
        assert len(await store.vault_audit_entries("creds_dispense")) == 5


class TestTheReadRoutesArePaged:
    async def test_newest_first_and_bounded(
        self, client: httpx.AsyncClient,
    ) -> None:
        store = client._app.state.store  # type: ignore[attr-defined]
        for i in range(10):
            await store.add_vault_audit("signing", {"i": i}, "t")
        page = (await client.get("/v1/vault/signing/audit?limit=3",
                                 headers=SH)).json()
        assert [r["i"] for r in page] == [9, 8, 7]
        nxt = (await client.get(
            f"/v1/vault/signing/audit?limit=3&before_id={page[-1]['audit_id']}",
            headers=SH)).json()
        assert [r["i"] for r in nxt] == [6, 5, 4]

    async def test_a_limit_of_zero_is_refused_not_reinterpreted(
        self, client: httpx.AsyncClient,
    ) -> None:
        """A `limit=0` that quietly means 200 is one thing; a `limit=0` that
        quietly means "none" is a caller reading an empty log and believing it."""
        r = await client.get("/v1/vault/signing/audit?limit=0", headers=SH)
        assert r.status_code == 400

    async def test_the_page_is_clamped(self, client: httpx.AsyncClient) -> None:
        from axor_backend.routers.vault import AUDIT_PAGE_MAX

        store = client._app.state.store  # type: ignore[attr-defined]
        for i in range(5):
            await store.add_vault_audit("creds_dispense", {"i": i}, "t")
        r = await client.get(
            f"/v1/vault/creds/audit?limit={AUDIT_PAGE_MAX * 10}", headers=CH)
        assert r.status_code == 200
        assert len(r.json()) == 5


class TestAnAttestationIsForTheCallBeingMade:
    """It signs a `timestamp` and nothing read it, so one captured attestation
    re-fetched its credential forever. The plane's commands are replay-guarded
    by the version counter; a dispense has no counter, so freshness is what it
    gets."""

    @staticmethod
    def _att(**over: object) -> dict:
        att = {"node_id": "n1", "tool": "pay", "endpoint": "https://pay",
               "timestamp": datetime.now(UTC).isoformat()}
        att.update(over)
        return att

    @staticmethod
    def _verify(att: dict) -> bool:
        from axor_backend.vault_dispense import verify

        return verify(att, node_id="n1", tool="pay", endpoint="https://pay",
                      pubkey_hex=None)

    def test_a_fresh_one_is_accepted(self) -> None:
        assert self._verify(self._att()) is False  # unsigned, but accepted

    @pytest.mark.parametrize(("what", "delta"), [
        ("seven years old", timedelta(days=365 * 7)),
        ("an hour old", timedelta(hours=1)),
    ])
    def test_a_stale_one_is_refused(self, what: str, delta: timedelta) -> None:
        from axor_backend.vault_dispense import AttestationRefused

        att = self._att(timestamp=(datetime.now(UTC) - delta).isoformat())
        with pytest.raises(AttestationRefused, match="old"):
            self._verify(att)

    def test_one_dated_forward_is_refused_too(self) -> None:
        """Not paranoia: an attestation dated forward is a pass that starts
        working later, minted before anyone thought to look."""
        from axor_backend.vault_dispense import AttestationRefused

        att = self._att(
            timestamp=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
        with pytest.raises(AttestationRefused, match="in the future"):
            self._verify(att)

    def test_a_timestamp_that_is_not_a_date_is_refused(self) -> None:
        """It is inside the signed envelope, so a signer that cannot produce a
        date is a signer whose statement cannot be placed in time at all."""
        from axor_backend.vault_dispense import AttestationRefused

        with pytest.raises(AttestationRefused, match="ISO-8601"):
            self._verify(self._att(timestamp="yesterday-ish"))

    def test_a_naive_timestamp_is_read_as_utc_not_refused(self) -> None:
        """A node that omits the offset is careless, not hostile, and the
        alternative is refusing every such node's credentials."""
        att = self._att(timestamp=datetime.now(UTC).replace(tzinfo=None).isoformat())
        assert self._verify(att) is False

    async def test_the_route_refuses_a_replayed_attestation(
        self, client: httpx.AsyncClient,
    ) -> None:
        await client.post("/v1/vault/creds/enroll", headers=CH, json={
            "tool": "pay", "endpoint": "https://pay", "secret": "s1",
            "scope_nodes": ["n1"]})
        stale = self._att(
            timestamp=(datetime.now(UTC) - timedelta(days=1)).isoformat())
        r = await client.post("/v1/vault/creds/dispense", headers=CH, json={
            "node_id": "n1", "tool": "pay", "endpoint": "https://pay",
            "attestation": stale})
        assert r.status_code == 403
        assert "window" in r.json()["detail"]


class TestTheUpgradeCarriesWhatIsAlreadyThere:
    """The blobs are the only record of what a deployment has already done, so
    0015 moves them row by row rather than dropping the keys and starting
    clean."""

    def test_a_0014_deployments_logs_survive(
        self, tmp_path: pathlib.Path,
    ) -> None:
        import json

        import sqlalchemy as sa
        from alembic import command
        from alembic.config import Config

        url = f"sqlite:///{tmp_path}/old.db"
        engine = sa.create_engine(url)
        cfg = Config()
        cfg.set_main_option(
            "script_location",
            str(pathlib.Path(__file__).resolve().parents[1]
                / "src" / "axor_backend" / "migrations"),
        )
        cfg.set_main_option("sqlalchemy.url", url)

        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "0014")
        with engine.begin() as conn:
            for key, blob in [
                ("vault_signing/audit/v1", [
                    {"operator": "op_alice", "key_id": "k_release",
                     "granted": True, "ts": "2026-09-01T00:00:00Z"},
                    {"operator": "mallory", "key_id": "probe", "granted": False,
                     "ts": "2026-09-02T00:00:00Z", "refusal": "unknown key"},
                ]),
                ("vault_creds/dispense_log/v1", [
                    {"node_id": "n1", "tool": "pay", "signed": False,
                     "ts": "2026-09-03T00:00:00Z"},
                ]),
            ]:
                conn.execute(sa.text(
                    "INSERT INTO settings (key, value, revision, org_id) "
                    "VALUES (:k, :v, 1, 'public')"),
                    {"k": key, "v": json.dumps(blob)})
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")

        with engine.connect() as conn:
            rows = conn.execute(sa.text(
                "SELECT kind, entry_json, created_ts FROM vault_audit "
                "ORDER BY id")).all()
            kinds = [r.kind for r in rows]
            assert sorted(kinds) == ["creds_dispense", "signing", "signing"]
            assert [r.created_ts for r in rows if r.kind == "signing"] == [
                "2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z"]
            entry = json.loads(
                [r.entry_json for r in rows if r.kind == "signing"][0])
            assert entry["key_id"] == "k_release" and entry["granted"] is True
            # and the blobs are gone, so nothing reads two sources of truth
            left = conn.execute(sa.text(
                "SELECT key FROM settings WHERE key IN "
                "('vault_signing/audit/v1', 'vault_creds/dispense_log/v1')"
            )).all()
            assert left == []
