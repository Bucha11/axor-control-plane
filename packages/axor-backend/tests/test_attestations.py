"""Operator attestations: recorded on the plane, given meaning by Sentinel.

The plane records who vouched for what. What that means — append-only,
revocation as an event rather than a deletion, a required reason and a required
operator identity — is `axor_sentinel.sentinel.attestation`'s, imported rather
than restated. Two of these classes are the defects that came from restating it.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.attestations import covering
from axor_backend.signing import signed_payload
from nacl.signing import SigningKey

OP = "op_dmitrii"


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey(b"\x01" * 32)


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    """Unsigned deployment — the plane's dev posture."""
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={}, allow_unsigned=True,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        yield c


@pytest.fixture
async def signed(
    tmp_path: pathlib.Path, signing_key: SigningKey
) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/signed.db",
        operator_keys={OP: signing_key.verify_key.encode().hex()},
        allow_unsigned=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c, app.router.lifespan_context(app):
        yield c


def _fact(fact_id: str, **over: object) -> dict:
    fact = {
        "fact_id": fact_id, "fact_type": "operator_attestation",
        "run_id": "run_a", "causal_root": "v_ext_1",
        "operator": OP, "reason": "checked by hand; the transfer is legitimate",
    }
    fact.update(over)
    return fact


async def _append(client: httpx.AsyncClient, fact: dict) -> httpx.Response:
    return await client.post("/v1/plane/n1/facts", json={"fact": fact})


async def _append_signed(
    client: httpx.AsyncClient, key: SigningKey, fact: dict, operator: str = OP
) -> httpx.Response:
    ts = datetime.now(UTC).isoformat()
    sig = key.sign(signed_payload("n1", 0, fact, ts)).signature.hex()
    return await client.post("/v1/plane/n1/facts", json={
        "fact": fact, "operator": operator, "timestamp": ts, "sig": sig})


# ── what the plane refuses to record ─────────────────────────────────────────

class TestTheAdmissionRuleIsSentinels:
    """`validate` is imported. A plane that accepted an attestation Sentinel
    would refuse would be storing a fact the two sides disagree about."""

    async def test_a_reason_is_required(self, client: httpx.AsyncClient) -> None:
        r = await _append(client, _fact("a1", reason=""))
        assert r.status_code == 400
        assert "reason" in r.json()["detail"]

    async def test_an_operator_identity_is_required(
        self, client: httpx.AsyncClient
    ) -> None:
        r = await _append(client, _fact("a2", operator=""))
        assert r.status_code == 400
        assert "operator" in r.json()["detail"]

    async def test_a_whitespace_reason_is_not_a_reason(
        self, client: httpx.AsyncClient
    ) -> None:
        assert (await _append(client, _fact("a3", reason="   "))).status_code == 400

    async def test_a_complete_attestation_is_recorded(
        self, client: httpx.AsyncClient
    ) -> None:
        assert (await _append(client, _fact("a4"))).status_code == 201


class TestAnAttestationNamesItsRun:
    """An attestation vouches two ways and both are minted per run: `covers`
    names fact ids (`deg_{seq}`, the kernel's contract) and `causal_root` names
    a value branch (`v_ext_1`, Sentinel's). Both counters restart at zero every
    run, so an attestation scoped to the bare name covered every other run's
    fact or ref of the same name — the operator-side laundering channel
    Sentinel's attestation module exists in order not to have."""

    async def test_an_attestation_without_a_run_is_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        fact = _fact("a5")
        del fact["run_id"]
        r = await _append(client, fact)
        assert r.status_code == 400
        assert "run_id" in r.json()["detail"]

    async def test_an_attestation_that_vouches_for_nothing_needs_no_run(
        self, client: httpx.AsyncClient
    ) -> None:
        """An attestation with neither `covers` nor `causal_root` is a recorded
        operator note on the node: nothing to place in a run, no fact
        discharged, and it appears on no branch's surface."""
        fact = _fact("a5b", causal_root="")
        del fact["run_id"]
        assert (await _append(client, fact)).status_code == 201
        assert (await client.get("/v1/runs/run_a/attestations",
                                 params={"ref": "v_ext_1"})).json() == []

    async def test_a_fact_id_without_a_run_is_refused_too(
        self, client: httpx.AsyncClient
    ) -> None:
        """`covers` names fact ids, which the trace bridge mints as `deg_{seq}`
        — also from a counter that restarts every run."""
        fact = _fact("a5c", causal_root="", covers=["deg_5"])
        del fact["run_id"]
        r = await _append(client, fact)
        assert r.status_code == 400
        assert "run_id" in r.json()["detail"]

    async def test_coverage_does_not_reach_another_runs_ref_of_the_same_name(
        self, client: httpx.AsyncClient
    ) -> None:
        assert (await _append(client, _fact("a6", run_id="run_monday"))).status_code == 201
        mine = (await client.get("/v1/runs/run_monday/attestations",
                                 params={"ref": "v_ext_1"})).json()
        assert [a["fact_id"] for a in mine] == ["a6"]
        theirs = (await client.get("/v1/runs/run_friday/attestations",
                                   params={"ref": "v_ext_1"})).json()
        assert theirs == []


class TestTheClaimedOperatorIsTheSigningOperator:
    """The signature covers the fact body, so the claimed operator and the
    signing one were always transmitted together — and nothing compared them.
    Attribution is the entire point of this surface, and it was the field that
    was not checked: anyone with a key could file an attestation under someone
    else's name."""

    async def test_a_fact_signed_by_one_operator_cannot_name_another(
        self, signed: httpx.AsyncClient, signing_key: SigningKey
    ) -> None:
        r = await _append_signed(signed, signing_key, _fact("a7", operator="op_someone_else"))
        assert r.status_code == 403
        assert "op_someone_else" in r.json()["detail"]

    async def test_a_fact_naming_its_own_signer_is_recorded(
        self, signed: httpx.AsyncClient, signing_key: SigningKey
    ) -> None:
        r = await _append_signed(signed, signing_key, _fact("a8"))
        assert r.status_code == 201


# ── what the plane returns ───────────────────────────────────────────────────

class TestRevocationIsApplied:
    """This surface stored `revokes` on every attestation, handed it to the UI,
    and never applied it: a revoked attestation kept covering its branch for
    good, and the UI printed "revokes" beside a coverage that had not changed.
    `effective_revocations` is Sentinel's, and had the rule the whole time."""

    async def test_a_revoked_attestation_stops_covering_its_branch(
        self, client: httpx.AsyncClient
    ) -> None:
        await _append(client, _fact("att"))
        await _append(client, _fact("rev", revokes="att",
                                    reason="the check was wrong"))
        atts = (await client.get("/v1/runs/run_a/attestations",
                                 params={"ref": "v_ext_1"})).json()
        by_id = {a["fact_id"]: a for a in atts}
        assert by_id["att"]["in_effect"] is False
        assert by_id["rev"]["revokes"] == "att"

    async def test_nothing_is_deleted_when_a_revocation_lands(
        self, client: httpx.AsyncClient
    ) -> None:
        """Append-only in both directions: the revoked attestation stays in the
        history, it just stops counting."""
        await _append(client, _fact("att"))
        await _append(client, _fact("rev", revokes="att", reason="wrong"))
        atts = (await client.get("/v1/runs/run_a/attestations",
                                 params={"ref": "v_ext_1"})).json()
        assert {a["fact_id"] for a in atts} == {"att", "rev"}

    def test_a_revocation_that_forgot_the_branch_still_applies(self) -> None:
        """A revocation is matched by the fact_id it names, not by repeating
        `covers`. Requiring the branch again would make a forgetful revocation a
        silent no-op — recorded, returned, and ignored."""
        facts = [
            _fact("att"),
            _fact("rev", revokes="att", causal_root="", reason="wrong"),
        ]
        by_id = {a["fact_id"]: a for a in covering(facts, "v_ext_1")}
        assert by_id["att"]["in_effect"] is False

    def test_a_revocation_of_some_other_branch_changes_nothing(self) -> None:
        facts = [
            _fact("att"),
            _fact("rev", revokes="elsewhere", reason="wrong"),
        ]
        out = covering(facts, "v_ext_1")
        assert [a["fact_id"] for a in out] == ["att"]
        assert out[0]["in_effect"] is True


class TestTheBranchHistory:
    async def test_two_operators_on_one_branch_are_both_kept(
        self, client: httpx.AsyncClient
    ) -> None:
        """An audit trail that collapses two people vouching for the same value
        is worse than none."""
        await _append(client, _fact("f1", operator="alice"))
        await _append(client, _fact("f2", operator="bob"))
        atts = (await client.get("/v1/runs/run_a/attestations",
                                 params={"ref": "v_ext_1"})).json()
        assert {a["operator"] for a in atts} == {"alice", "bob"}
        assert all(a["in_effect"] for a in atts)

    async def test_newest_first(self, client: httpx.AsyncClient) -> None:
        await _append(client, _fact("older"))
        await _append(client, _fact("newer"))
        atts = (await client.get("/v1/runs/run_a/attestations",
                                 params={"ref": "v_ext_1"})).json()
        assert [a["fact_id"] for a in atts] == ["newer", "older"]

    async def test_a_branch_nobody_attested_has_no_history(
        self, client: httpx.AsyncClient
    ) -> None:
        await _append(client, _fact("f1"))
        atts = (await client.get("/v1/runs/run_a/attestations",
                                 params={"ref": "v_model_2"})).json()
        assert atts == []

    async def test_other_fact_types_are_not_attestations(
        self, client: httpx.AsyncClient
    ) -> None:
        """Facts are also where every node's degradation transition and heat
        crossing land. Only attestations are read back here."""
        await _append(client, {
            "fact_id": "deg1", "fact_type": "degradation_transition",
            "run_id": "run_a", "causal_root": "v_ext_1", "severity": 1,
        })
        assert (await client.get("/v1/runs/run_a/attestations",
                                 params={"ref": "v_ext_1"})).json() == []
