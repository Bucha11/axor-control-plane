"""Degradation coverage: what an attestation discharges, and what it costs.

`covers` names FACT IDS — the kernel says so in `Fact`'s own docstring — and
`axor_core.kernel.degradation` says what covering them does:
`level = max(severity(uncovered facts))`. Both are imported; none of it is
restated here, and these tests exist to prove the plane did not grow a second
opinion.

This is what makes "attest branch" an action rather than a gesture: before it,
the button appended a fact that covered nothing, discharged nothing and
appeared nowhere.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_core.kernel.events import SCHEMA_VERSION

NODE = "n_governed"
OP = "op_ui"


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


def _line(seq: int, kind: str, *, causal_root: str | None = None, **payload: object) -> dict:
    return {"schema_version": SCHEMA_VERSION, "seq": seq, "node_id": NODE,
            "kind": kind, "ts": "t", "causal_root": causal_root,
            "payload": payload}


def _quarantine(seq: int, fact_id: str, severity: int, root: str) -> dict:
    """The shape axor-wrap's trace bridge produces for a quarantined source."""
    return _line(seq, "fact", causal_root=root, fact_id=fact_id,
                 fact_type="source_quarantined", severity=severity,
                 reason="untrusted source quarantined")


async def _report(client: httpx.AsyncClient, *lines: dict, level: str = "RESTRICTED",
                  run_id: str = "run_live") -> None:
    body = {"run_id": run_id, "events": [
        *lines,
        _line(900, "heartbeat", level=level, applied_version=0),
    ]}
    r = await client.post(f"/v1/plane/{NODE}/telemetry", json=body,
                          headers={"Idempotency-Key": f"k{len(lines)}{level}{run_id}"})
    assert r.status_code == 202, r.text


async def _attest(client: httpx.AsyncClient, fact_id: str, covers: list[str],
                  **over: object) -> httpx.Response:
    fact = {"fact_id": fact_id, "fact_type": "operator_attestation",
            "run_id": "run_live", "covers": covers, "operator": OP,
            "reason": "reviewed the source; it is our own canary"}
    fact.update(over)
    return await client.post(f"/v1/plane/{NODE}/facts", json={"fact": fact})


async def _coverage(client: httpx.AsyncClient) -> dict:
    r = await client.get(f"/v1/plane/{NODE}/coverage")
    assert r.status_code == 200, r.text
    return r.json()


# ── the level is the kernel's recompute over coverage ────────────────────────

async def test_a_driving_fact_is_named_with_the_branch_it_is_about(
    client: httpx.AsyncClient
) -> None:
    """`causal_root` is a column on the kernel Event, not a field of `Fact`, so
    it does not survive the fold — and it is the only thing that tells an
    operator which branch a degradation fact is about."""
    await _report(client, _quarantine(0, "quar_0", 2, "v_ext_1"))
    body = await _coverage(client)
    assert body["run_id"] == "run_live"
    assert body["level"] == "RESTRICTED"
    fact = next(f for f in body["facts"] if f["fact_id"] == "quar_0")
    assert fact["causal_root"] == "v_ext_1"
    assert fact["severity"] == 2
    assert fact["covered_by"] == []


async def test_covering_the_driving_fact_lowers_the_level(
    client: httpx.AsyncClient
) -> None:
    await _report(client, _quarantine(0, "quar_0", 2, "v_ext_1"))
    assert (await _coverage(client))["level"] == "RESTRICTED"
    assert (await _attest(client, "att1", ["quar_0"])).status_code == 201
    body = await _coverage(client)
    assert body["level"] == "NORMAL"
    assert body["covered"] == ["quar_0"]
    assert next(f for f in body["facts"] if f["fact_id"] == "quar_0")["covered_by"] == [OP]


async def test_the_worst_uncovered_fact_sets_the_level(
    client: httpx.AsyncClient
) -> None:
    """`max(severity(uncovered))`: covering the worst one exposes the next."""
    await _report(client, _quarantine(0, "quar_0", 3, "v_a"),
                  _quarantine(1, "quar_1", 1, "v_b"))
    assert (await _coverage(client))["level"] == "LOCKED"
    await _attest(client, "att1", ["quar_0"])
    assert (await _coverage(client))["level"] == "CAUTIOUS"
    await _attest(client, "att2", ["quar_1"])
    assert (await _coverage(client))["level"] == "NORMAL"


async def test_a_revoked_attestation_stops_discharging_its_fact(
    client: httpx.AsyncClient
) -> None:
    await _report(client, _quarantine(0, "quar_0", 2, "v_ext_1"))
    await _attest(client, "att1", ["quar_0"])
    assert (await _coverage(client))["level"] == "NORMAL"
    await _attest(client, "rev1", [], revokes="att1", causal_root="v_ext_1",
                  reason="the review was wrong")
    body = await _coverage(client)
    assert body["level"] == "RESTRICTED"
    assert next(f for f in body["facts"] if f["fact_id"] == "quar_0")["covered_by"] == []


async def test_an_attestation_from_another_run_discharges_nothing(
    client: httpx.AsyncClient
) -> None:
    """Fact ids are minted per run (`quar_{seq}`), so an attestation naming
    `quar_0` in one run must not discharge another run's `quar_0`. This is the
    whole reason run_id is required."""
    await _report(client, _quarantine(0, "quar_0", 2, "v_ext_1"))
    r = await _attest(client, "att_elsewhere", ["quar_0"], run_id="run_other")
    assert r.status_code == 201
    assert (await _coverage(client))["level"] == "RESTRICTED"


# ── the node's word is never overwritten ─────────────────────────────────────

async def test_the_reported_level_stays_the_nodes_own(
    client: httpx.AsyncClient
) -> None:
    """Attesting is not a write to the node's state. The node converges when the
    fact reaches it on its desired-state stream; until then the plane renders
    the divergence rather than hiding it (protocol, section 5)."""
    await _report(client, _quarantine(0, "quar_0", 2, "v_ext_1"))
    await _attest(client, "att1", ["quar_0"])
    body = await _coverage(client)
    assert body["reported_level"] == "RESTRICTED"
    assert body["level"] == "NORMAL"


# ── the honest edges ─────────────────────────────────────────────────────────

async def test_a_node_that_has_never_reported_has_nothing_to_attest(
    client: httpx.AsyncClient
) -> None:
    """Not a 404. "No facts, level NORMAL, nothing to attest" is the correct
    answer about a node the plane has not heard from."""
    body = await _coverage(client)
    assert body == {"node_id": NODE, "run_id": None, "reported_level": "NORMAL",
                    "level": "NORMAL", "facts": [], "covered": []}


async def test_a_run_carrying_only_plane_telemetry_is_not_an_error(
    client: httpx.AsyncClient
) -> None:
    """A client that is not axor-wrap's plane client posts lines with no kernel
    schema. Replay refuses such a run (422, "plane telemetry only"); this view
    must not — the node recorded no facts, and that is an answer, not an error.
    """
    r = await client.post(f"/v1/plane/{NODE}/telemetry", json={
        "run_id": "run_live",
        "events": [{"seq": 0, "kind": "heartbeat",
                    "payload": {"applied_version": 0, "level": "NORMAL"}}],
    })
    assert r.status_code == 202, r.text
    assert (await client.get("/v1/replay/run_live")).status_code == 422
    body = await _coverage(client)
    assert body["run_id"] == "run_live"
    assert body["facts"] == []
    assert body["level"] == "NORMAL"


async def test_a_healthy_kernel_run_has_nothing_to_attest(
    client: httpx.AsyncClient
) -> None:
    """The ordinary case: a governed node heartbeating with nothing wrong."""
    await _report(client, level="NORMAL")
    body = await _coverage(client)
    assert body["run_id"] == "run_live"
    assert body["facts"] == []
    assert body["level"] == "NORMAL"


async def test_a_trace_that_cannot_be_read_is_not_reported_as_healthy(
    client: httpx.AsyncClient
) -> None:
    """Answering "no facts" for an unreadable trace would report a degraded node
    as healthy — the plane's one job, told backwards."""
    await client.post(f"/v1/plane/{NODE}/telemetry", json={
        "run_id": "run_live",
        "events": [{"schema_version": "99.0", "seq": 0, "node_id": NODE,
                    "kind": "heartbeat", "ts": "t", "payload": {}}],
    })
    r = await client.get(f"/v1/plane/{NODE}/coverage")
    assert r.status_code == 422
    assert "not a replayable kernel trace" in r.json()["detail"]
