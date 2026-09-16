"""An attestation covers a fact. It must not be able to replace one.

`coverage` merges the run's recorded facts with the plane's attestation facts
by fact id, and `append_fact` enforces append-only only WITHIN the fact log —
the run's own facts live in the event log, a different table with no shared
uniqueness. So an attestation could take a recorded fact's id, and the merge
resolved in its favour. Since `compute_level` skips attestations entirely, the
degradation that fact carried stopped counting:

    with a second, severity-4 fact   level=TERMINAL  facts=[('quar_1',3),('quar_9',4)]
    a note whose fact_id is quar_9   -> 201 {"appended":true}
    after it                         level=NORMAL    facts=[('quar_1',3)]

`covers` was empty on that note — it vouched for nothing. The fact did not read
as discharged, it was gone, and the panel's empty-state then says "nothing is
holding this node down". That is descent by deletion, which
`axor_sentinel.sentinel.attestation` opens by ruling out: "a reset implemented
as deletion or zeroing would be an operator-side reputation-laundering channel,
so reset does not exist".

The second half is the same rule one step short: the door already demands
`run_id` whenever an attestation vouches for anything, because fact ids are
unique only inside the run that minted them — but it never checked the run
HAD the ids. A transposed character answered 201, discharged nothing, and came
back listed under `covered`.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_core.kernel.events import SCHEMA_VERSION

NODE = "n_governed"
OP = "op_ui"
RUN = "run_live"


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={}, allow_unsigned=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        c._app = app  # type: ignore[attr-defined]
        yield c


def _line(seq: int, kind: str, *, causal_root: str | None = None,
          **payload: object) -> dict:
    return {"schema_version": SCHEMA_VERSION, "seq": seq, "node_id": NODE,
            "kind": kind, "ts": "t", "causal_root": causal_root,
            "payload": payload}


async def _quarantine(c: httpx.AsyncClient, seq: int, fact_id: str,
                      severity: int, root: str) -> None:
    r = await c.post(f"/v1/plane/{NODE}/telemetry", json={
        "run_id": RUN,
        "events": [
            _line(seq, "fact", causal_root=root, fact_id=fact_id,
                  fact_type="source_quarantined", severity=severity,
                  reason="untrusted source quarantined"),
            _line(900 + seq, "heartbeat", level="RESTRICTED", applied_version=0),
        ],
    }, headers={"Idempotency-Key": f"k{seq}"})
    assert r.status_code == 202, r.text


async def _fact(c: httpx.AsyncClient, **over: object) -> httpx.Response:
    fact: dict = {"fact_type": "operator_attestation", "run_id": RUN,
                  "operator": OP, "reason": "reviewed; our own canary"}
    fact.update(over)
    return await c.post(f"/v1/plane/{NODE}/facts", json={"fact": fact})


async def _cov(c: httpx.AsyncClient) -> dict:
    return (await c.get(f"/v1/plane/{NODE}/coverage")).json()


class TestAnAttestationCannotTakeARecordedFactsId:
    async def test_the_collision_is_refused(
        self, client: httpx.AsyncClient,
    ) -> None:
        await _quarantine(client, 1, "quar_1", 4, "v_ext_1")
        r = await _fact(client, fact_id="quar_1", covers=[])
        assert r.status_code == 409
        assert "COVERS a fact" in r.json()["detail"]

    async def test_the_level_survives_the_attempt(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The measurement that made this a finding rather than a tidy-up."""
        await _quarantine(client, 1, "quar_9", 4, "v_ext_1")
        assert (await _cov(client))["level"] == "TERMINAL"
        await _fact(client, fact_id="quar_9", covers=[])
        body = await _cov(client)
        assert body["level"] == "TERMINAL"
        assert [f["fact_id"] for f in body["facts"]] == ["quar_9"]

    async def test_a_fact_already_shadowed_is_not_obeyed(
        self, client: httpx.AsyncClient,
    ) -> None:
        """Rows stored before the door existed. The merge resolves for the RUN,
        so the recorded degradation stands and the colliding note does nothing —
        the safe direction, and the only one that keeps the audit row."""
        import json

        from axor_backend.coverage import coverage
        from axor_backend.replay_api import parse_trace

        events = parse_trace([json.dumps(
            _line(1, "fact", causal_root="v_ext_1", fact_id="quar_9",
                  fact_type="source_quarantined", severity=4, reason="q")
        )])
        report = coverage(events, [{
            "fact_id": "quar_9", "fact_type": "operator_attestation",
            "run_id": RUN, "operator": OP, "reason": "note", "covers": [],
        }])
        assert report["level"] == "TERMINAL"
        assert [f["fact_id"] for f in report["facts"]] == ["quar_9"]

    async def test_a_shadowing_row_is_logged_not_silent(
        self, client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture,
    ) -> None:
        import json

        from axor_backend.coverage import coverage
        from axor_backend.replay_api import parse_trace

        events = parse_trace([json.dumps(
            _line(1, "fact", fact_id="quar_9", fact_type="source_quarantined",
                  severity=4, reason="q")
        )])
        with caplog.at_level("WARNING"):
            coverage(events, [{
                "fact_id": "quar_9", "fact_type": "operator_attestation",
                "operator": OP, "reason": "note", "covers": [],
            }])
        assert any("share an id" in r.getMessage() for r in caplog.records)

    async def test_a_normal_attestation_still_works(
        self, client: httpx.AsyncClient,
    ) -> None:
        await _quarantine(client, 1, "quar_1", 3, "v_ext_1")
        r = await _fact(client, fact_id="att_1", covers=["quar_1"],
                        causal_root="v_ext_1")
        assert r.status_code == 201
        body = await _cov(client)
        assert body["level"] == "NORMAL"
        assert body["covered"] == ["quar_1"]
        assert body["facts"][0]["covered_by"] == [OP]


class TestCoversMustNameAFactTheRunHas:
    async def test_a_transposed_id_is_refused(
        self, client: httpx.AsyncClient,
    ) -> None:
        await _quarantine(client, 1, "quar_1", 3, "v_ext_1")
        r = await _fact(client, fact_id="att_typo", covers=["qaur_1"])
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "qaur_1" in detail
        assert "quar_1" in detail  # says what it COULD have covered

    async def test_it_no_longer_answers_201_and_does_nothing(
        self, client: httpx.AsyncClient,
    ) -> None:
        await _quarantine(client, 1, "quar_1", 3, "v_ext_1")
        await _fact(client, fact_id="att_typo", covers=["qaur_1"])
        body = await _cov(client)
        assert body["level"] == "LOCKED"
        assert body["covered"] == []  # was ['qaur_1'] — an id no fact has

    def test_covered_never_lists_an_id_no_fact_has(self) -> None:
        """For rows stored before the door existed — the only way to get here
        now. `covered` is the kernel's answer about the ids an attestation
        NAMED, so a transposed one came back listed as covered while the fact it
        meant to discharge stayed at full severity: an operator reading the list
        would see their attestation had landed."""
        import json

        from axor_backend.coverage import coverage
        from axor_backend.replay_api import parse_trace

        events = parse_trace([json.dumps(
            _line(1, "fact", fact_id="quar_1", fact_type="source_quarantined",
                  severity=3, reason="q")
        )])
        report = coverage(events, [{
            "fact_id": "att_typo", "fact_type": "operator_attestation",
            "operator": OP, "reason": "reviewed", "covers": ["qaur_1"],
        }])
        assert report["covered"] == []
        assert report["level"] == "LOCKED"

    async def test_attesting_a_fact_that_has_not_happened_yet(
        self, client: httpx.AsyncClient,
    ) -> None:
        """Same refusal, and it is the point rather than a side effect:
        pre-attesting is "trust this forever", which is what Sentinel's
        "I checked, resume watching" exists instead of."""
        await _quarantine(client, 1, "quar_1", 3, "v_ext_1")
        r = await _fact(client, fact_id="att_ahead", covers=["quar_2"])
        assert r.status_code == 400
        await _quarantine(client, 2, "quar_2", 4, "v_ext_2")
        assert (await _cov(client))["level"] == "TERMINAL"

    async def test_covering_several_facts_at_once_still_works(
        self, client: httpx.AsyncClient,
    ) -> None:
        await _quarantine(client, 1, "quar_1", 3, "v_ext_1")
        await _quarantine(client, 2, "quar_2", 4, "v_ext_2")
        r = await _fact(client, fact_id="att_both",
                        covers=["quar_1", "quar_2"])
        assert r.status_code == 201
        assert (await _cov(client))["level"] == "NORMAL"

    async def test_a_revocation_carries_no_covers_and_is_unaffected(
        self, client: httpx.AsyncClient,
    ) -> None:
        await _quarantine(client, 1, "quar_1", 3, "v_ext_1")
        await _fact(client, fact_id="att_1", covers=["quar_1"],
                    causal_root="v_ext_1")
        r = await _fact(client, fact_id="rev_1", revokes="att_1",
                        causal_root="v_ext_1", reason="I was wrong")
        assert r.status_code == 201
        assert (await _cov(client))["level"] == "LOCKED"

    async def test_a_note_that_vouches_for_nothing_needs_no_run(
        self, client: httpx.AsyncClient,
    ) -> None:
        """The door's existing carve-out has to survive: an attestation with
        neither `covers` nor `causal_root` is an operator note on the node."""
        r = await client.post(f"/v1/plane/{NODE}/facts", json={"fact": {
            "fact_id": "note_1", "fact_type": "operator_attestation",
            "operator": OP, "reason": "spoke to the vendor",
        }})
        assert r.status_code == 201

    async def test_an_unreadable_run_refuses_rather_than_guesses(
        self, client: httpx.AsyncClient,
    ) -> None:
        """If the trace cannot be read the check cannot run, and an attestation
        the plane stores but cannot place is what `_check_attestation` already
        refuses to take."""
        store = client._app.state.store  # type: ignore[attr-defined]
        await store.upsert_run("run_bad", NODE, "probe", "t")
        await store.ingest_events("run_bad", NODE, [
            {"schema_version": "99.0", "seq": 0, "node_id": NODE,
             "kind": "heartbeat", "ts": "t", "payload": {}},
        ], None)
        r = await _fact(client, fact_id="att_x", covers=["quar_1"],
                        run_id="run_bad")
        assert r.status_code == 400
        assert "does not read back" in r.json()["detail"]


class TestCoveredByAgreesWithTheKernel:
    """`coverage` re-states one line of `axor_core.kernel.degradation`:

        revoked = {f.revokes for f in facts.values() if f.revokes is not None}

    It has to, because `covered_by` is per-target attribution — WHICH operator
    discharged this fact — and the kernel exports only the flat set of covered
    ids. But a restatement is a thing that can drift, and the sibling module
    says so in its own words: "Restating it is what went wrong." Sentinel's
    revocation rule already differs (a revocation counts only from the attesting
    keyset); it collapses to the kernel's here only because the plane's keyring
    is one keyset and `record_from_fact` passes `org=""`. If either moves, this
    test fails instead of `covered_by` and `level` quietly disagreeing.
    """

    @staticmethod
    def _report(*plane_facts: dict) -> dict:
        import json

        from axor_backend.coverage import coverage
        from axor_backend.replay_api import parse_trace

        events = parse_trace([
            json.dumps(_line(1, "fact", fact_id="f_a",
                             fact_type="source_quarantined", severity=3,
                             reason="a")),
            json.dumps(_line(2, "fact", fact_id="f_b",
                             fact_type="source_quarantined", severity=4,
                             reason="b")),
        ])
        return coverage(events, list(plane_facts))

    @staticmethod
    def _attestation(fact_id: str, **over: object) -> dict:
        fact = {"fact_id": fact_id, "fact_type": "operator_attestation",
                "operator": OP, "reason": "reviewed", "covers": []}
        fact.update(over)
        return fact

    def test_an_attributed_fact_is_a_covered_fact(self) -> None:
        report = self._report(self._attestation("att_1", covers=["f_a"]))
        attributed = {f["fact_id"] for f in report["facts"] if f["covered_by"]}
        assert attributed == set(report["covered"]) == {"f_a"}

    def test_a_revoked_attestation_attributes_nothing_and_covers_nothing(
        self,
    ) -> None:
        report = self._report(
            self._attestation("att_1", covers=["f_a"]),
            self._attestation("rev_1", revokes="att_1"),
        )
        assert report["covered"] == []
        assert all(not f["covered_by"] for f in report["facts"])
        assert report["level"] == "TERMINAL"  # f_b, severity 4, uncovered

    def test_a_live_attestation_is_not_dragged_down_by_a_revoked_sibling(
        self,
    ) -> None:
        """The two must agree fact by fact, not just in total."""
        report = self._report(
            self._attestation("att_1", covers=["f_a"]),
            self._attestation("att_2", covers=["f_b"]),
            self._attestation("rev_1", revokes="att_1"),
        )
        assert report["covered"] == ["f_b"]
        by_id = {f["fact_id"]: f["covered_by"] for f in report["facts"]}
        assert by_id["f_a"] == [] and by_id["f_b"] == [OP]

    def test_two_operators_covering_one_fact_are_both_named(self) -> None:
        report = self._report(
            self._attestation("att_1", covers=["f_a"], operator="op_one"),
            self._attestation("att_2", covers=["f_a"], operator="op_two"),
        )
        by_id = {f["fact_id"]: f["covered_by"] for f in report["facts"]}
        assert sorted(by_id["f_a"]) == ["op_one", "op_two"]


class TestTheExpensiveCheckIsBehindTheSignature:
    """`_check_run_scope` reads the whole run back. A caller who cannot produce
    an operator signature should not be able to spend that, so it runs after the
    keyring check rather than before it — an ordering that is invisible in the
    happy path and is the difference between a 403 and a full trace read on the
    unhappy one."""

    async def test_a_bad_signature_is_refused_before_the_run_is_read(
        self, tmp_path: pathlib.Path,
    ) -> None:
        from nacl.signing import SigningKey

        key = SigningKey(b"\x01" * 32)
        app = create_app(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/signed.db",
            operator_keys={OP: key.verify_key.encode().hex()},
            allow_unsigned=False,
        )
        reads: list[str] = []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t",
        ) as c, app.router.lifespan_context(app):
            real = app.state.store.run_events

            async def counted(run_id: str) -> list[str]:
                reads.append(run_id)
                return await real(run_id)

            app.state.store.run_events = counted
            r = await c.post(f"/v1/plane/{NODE}/facts", json={
                "fact": {"fact_id": "att_1", "fact_type": "operator_attestation",
                         "run_id": RUN, "covers": ["quar_1"], "operator": OP,
                         "reason": "reviewed"},
                "operator": OP, "timestamp": "t", "sig": "00" * 64,
            })
            assert r.status_code == 403
            assert reads == []
