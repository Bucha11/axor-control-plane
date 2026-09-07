"""Backend: plane service semantics, ingest, replay/regression endpoints."""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.signing import jcs_canonical, signed_payload
from nacl.signing import SigningKey

OP = "op_dmitrii"


@pytest.fixture
def signing_key() -> SigningKey:
    return SigningKey(b"\x01" * 32)


@pytest.fixture
async def client(tmp_path: pathlib.Path, signing_key: SigningKey) -> httpx.AsyncClient:
    app = create_app(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/axor.db",
        operator_keys={OP: signing_key.verify_key.encode().hex()},
        allow_unsigned=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    ) as c:
        c._app = app  # type: ignore[attr-defined]
        # trigger startup (table creation)
        async with app.router.lifespan_context(app):
            yield c


def _sign(key: SigningKey, node_id: str, version: int, body: dict, ts: str) -> str:
    return key.sign(signed_payload(node_id, version, body, ts)).signature.hex()


def _cmd(key: SigningKey, node_id: str, version: int, state: dict) -> dict:
    ts = datetime.now(UTC).isoformat()
    return {
        "version": version, "state": state, "operator": OP,
        "timestamp": ts, "sig": _sign(key, node_id, version, state, ts),
    }


TRACE = [
    {"schema_version": "1.0", "seq": 0, "node_id": "n0", "kind": "tool_call",
     "ts": "t", "causal_root": None, "gate": None, "verdict": "pass",
     "payload": {"tool": "email_read", "args": {}, "arg_refs": {}}},
    {"schema_version": "1.0", "seq": 1, "node_id": "n0", "kind": "tool_result",
     "ts": "t", "causal_root": None, "gate": None, "verdict": None,
     "payload": {"tool": "email_read", "value_ref": "v_mail",
                 "root": {"sources": ["web"], "sensitive": False}}},
    # denied because slack_post is a DECLARED egress sink (not because the
    # normalizer saw an external destination) — so removing the declaration
    # is a real counterfactual.
    {"schema_version": "1.0", "seq": 2, "node_id": "n0", "kind": "tool_call",
     "ts": "t", "causal_root": None, "gate": None, "verdict": "deny",
     "payload": {"tool": "slack_post", "args": {"text": "x"},
                 "arg_refs": {"text": "v_mail"}}},
]

CONFIG = {
    "allowed_tools": ["email_read", "slack_post"],
    "egress_sinks": ["slack_post"],
}


async def _ingest_demo(client: httpx.AsyncClient, run_id: str = "run_x") -> None:
    resp = await client.post(f"/v1/ingest/{run_id}", json={
        "node_id": "n0", "scenario": "demo", "events": TRACE,
    })
    assert resp.status_code == 202
    assert resp.json()["stored"] == 3


# ── JCS + signing ─────────────────────────────────────────────────────────────

def test_jcs_canonical_form() -> None:
    assert jcs_canonical({"b": 1, "a": [True, None, "ü"]}) == (
        '{"a":[true,null,"ü"],"b":1}'.encode()
    )


def test_jcs_rejects_floats() -> None:
    from axor_backend.errors import CommandRejected

    with pytest.raises(CommandRejected):
        jcs_canonical({"x": 1.5})


# ── plane commands ────────────────────────────────────────────────────────────

async def test_signed_command_bumps_version_and_stale_rejected(
    client: httpx.AsyncClient, signing_key: SigningKey
) -> None:
    resp = await client.post("/v1/plane/n0/command",
                             json=_cmd(signing_key, "n0", 1, {"paused": True}))
    assert resp.status_code == 202
    assert resp.json() == {"node_id": "n0", "version": 1,
                           "state": {"paused": True}}
    # stale version -> 409
    resp = await client.post("/v1/plane/n0/command",
                             json=_cmd(signing_key, "n0", 1, {"stopped": True}))
    assert resp.status_code == 409
    # correct next version merges LWW
    resp = await client.post("/v1/plane/n0/command",
                             json=_cmd(signing_key, "n0", 2, {"stopped": True}))
    assert resp.json()["state"] == {"paused": True, "stopped": True}


async def test_bad_signature_rejected(
    client: httpx.AsyncClient, signing_key: SigningKey
) -> None:
    body = _cmd(signing_key, "n0", 1, {"paused": True})
    body["sig"] = "00" * 64
    resp = await client.post("/v1/plane/n0/command", json=body)
    assert resp.status_code == 403


async def test_unknown_operator_rejected(client: httpx.AsyncClient) -> None:
    rogue = SigningKey(b"\x02" * 32)
    ts = datetime.now(UTC).isoformat()
    body = {
        "version": 1, "state": {"paused": True}, "operator": "op_rogue",
        "timestamp": ts,
        "sig": rogue.sign(
            signed_payload("n0", 1, {"paused": True}, ts)
        ).signature.hex(),
    }
    resp = await client.post("/v1/plane/n0/command", json=body)
    assert resp.status_code == 403


async def test_attestation_requires_reason_and_is_append_only(
    client: httpx.AsyncClient, signing_key: SigningKey
) -> None:
    ts = datetime.now(UTC).isoformat()
    fact = {"fact_id": "a1", "fact_type": "operator_attestation",
            "severity": 0, "run_id": "r0", "covers": ["f1"],
            "operator": OP, "reason": ""}
    body = {"fact": fact, "operator": OP, "timestamp": ts,
            "sig": _sign(signing_key, "n0", 0, fact, ts)}
    resp = await client.post("/v1/plane/n0/facts", json=body)
    assert resp.status_code == 400  # reason required (decision 8)

    fact["reason"] = "investigated; canary was ours"
    body["sig"] = _sign(signing_key, "n0", 0, fact, ts)
    resp = await client.post("/v1/plane/n0/facts", json=body)
    assert resp.status_code == 201
    # append-only: same fact_id refused
    resp = await client.post("/v1/plane/n0/facts", json=body)
    assert resp.status_code == 409


async def test_telemetry_heartbeat_updates_reported_and_dedupes(
    client: httpx.AsyncClient,
) -> None:
    batch = {
        "run_id": "run_t", "events": [
            {"schema_version": "1.0", "seq": 0, "node_id": "n0",
             "kind": "heartbeat", "ts": "t", "causal_root": None,
             "gate": None, "verdict": None,
             "payload": {"applied_version": 3, "level": "CAUTIOUS",
                         "budget_remaining": 42}},
        ],
    }
    r1 = await client.post("/v1/plane/n0/telemetry", json=batch,
                           headers={"Idempotency-Key": "k1"})
    assert r1.json()["stored"] == 1
    r2 = await client.post("/v1/plane/n0/telemetry", json=batch,
                           headers={"Idempotency-Key": "k1"})
    assert r2.json()["stored"] == 0  # duplicate batch dropped

    nodes = (await client.get("/v1/plane/nodes")).json()
    node = next(n for n in nodes if n["node_id"] == "n0")
    assert node["reported"]["applied_version"] == 3
    assert node["reported"]["level"] == "CAUTIOUS"


# ── ingest + replay + regression ──────────────────────────────────────────────

async def test_ingest_and_scrubber(client: httpx.AsyncClient) -> None:
    await _ingest_demo(client)
    scrub = (await client.get("/v1/replay/run_x")).json()
    assert scrub["first_divergence"] is None
    assert len(scrub["steps"]) == 3
    assert scrub["steps"][2]["state"]["tainted_refs"] == ["v_mail"]


async def test_counterfactual_endpoint_finds_divergence(
    client: httpx.AsyncClient,
) -> None:
    await _ingest_demo(client)
    # config without slack_post as egress: the recorded deny re-evaluates PASS
    resp = await client.post("/v1/replay/run_x", json={
        "config": {"allowed_tools": ["email_read", "slack_post"],
                   "egress_sinks": []},
    })
    data = resp.json()
    assert data["first_divergence"] == 2
    assert data["steps"][2]["reevaluated_verdict"] == "pass"


async def test_replay_of_telemetry_only_run_is_422_not_500(
    client: httpx.AsyncClient,
) -> None:
    """A governed node's keepalive run stores only heartbeats (no kernel
    schema_version) — replay must answer honestly, not crash in the kernel."""
    await client.post("/v1/plane/nX/telemetry", json={
        "run_id": "nX-live",
        "events": [{"seq": 0, "kind": "heartbeat",
                    "payload": {"applied_version": 0, "level": "NORMAL",
                                "budget_remaining": None}}],
    })
    resp = await client.get("/v1/replay/nX-live")
    assert resp.status_code == 422
    assert "no kernel-schema events" in resp.json()["detail"]


async def test_telemetry_requires_only_ingest_scope() -> None:
    """The adapter reports in with an ingest key; heartbeats must not demand
    the operator's `operate` scope (auth matrix, architecture §9)."""
    from axor_backend.auth import required_scope

    assert required_scope("POST", "/v1/plane/n1/telemetry") == "ingest"
    assert required_scope("POST", "/v1/plane/n1/consumed") == "ingest"
    # Operator actions stay operate.
    assert required_scope("POST", "/v1/plane/n1/command") == "operate"
    assert required_scope("POST", "/v1/plane/n1/facts") == "operate"
    assert required_scope("POST", "/v1/plane/n1/cascade-stop") == "operate"


async def test_regression_report_both_sides(client: httpx.AsyncClient) -> None:
    await _ingest_demo(client, "run_attack")
    # a legitimate trace: export of an untainted value
    legit = [
        {"schema_version": "1.0", "seq": 0, "node_id": "n0",
         "kind": "tool_call", "ts": "t", "causal_root": None, "gate": None,
         "verdict": "pass",
         "payload": {"tool": "slack_post", "args": {"text": "weekly"},
                     "arg_refs": {}}},
    ]
    await client.post("/v1/ingest/run_legit", json={
        "node_id": "n0", "events": legit,
    })
    await client.post("/v1/pins/run_attack",
                      json={"side": "must_block", "label": "exfil via slack"})
    await client.post("/v1/pins/run_legit",
                      json={"side": "must_pass", "label": "weekly report"})

    # The North-star surface: the corpus as counts (GET /v1/pins).
    corpus = (await client.get("/v1/pins")).json()
    assert corpus["total"] == 2
    assert corpus["must_block"] == 1
    assert corpus["must_pass"] == 1
    assert {p["run_id"] for p in corpus["pins"]} == {"run_attack", "run_legit"}

    good = (await client.post("/v1/regression", json={"config": CONFIG})).json()
    assert good["safe_to_ship"] is True
    assert {r["result"] for r in good["rows"]} == {"held", "passed"}

    # a config that blocks everything: attack held, legit regressed
    strict = {"allowed_tools": [], "egress_sinks": []}
    bad = (await client.post("/v1/regression", json={"config": strict})).json()
    assert bad["safe_to_ship"] is False
    by_id = {r["run_id"]: r for r in bad["rows"]}
    assert by_id["run_attack"]["result"] == "held"
    assert by_id["run_legit"]["result"] == "regressed"
    assert by_id["run_legit"]["new_denial"]["category"] == "capability"


async def test_two_commands_signed_for_one_version_cannot_both_apply(
    client: httpx.AsyncClient, signing_key: SigningKey,
) -> None:
    """The operator signature covers (node_id, version, delta, timestamp), so a
    command may only land at the version it was signed for.

    The route's own stale-version check is check-then-act — two commands
    claiming version 1 both read "expected 1" and both passed it. The store used
    to merge them into versions 1 and 2, which meant the second command was
    stored under a version its signature does not cover: the audit record and
    the signature disagree. Now the loser is refused and re-signs.
    """
    import asyncio

    both = await asyncio.gather(
        client.post("/v1/plane/n_sig/command",
                    json=_cmd(signing_key, "n_sig", 1, {"a": 1})),
        client.post("/v1/plane/n_sig/command",
                    json=_cmd(signing_key, "n_sig", 1, {"b": 2})),
        return_exceptions=True,
    )
    codes = sorted(r.status_code for r in both)
    assert codes == [202, 409], f"expected one accept and one refusal, got {codes}"

    winner = next(r for r in both if r.status_code == 202).json()
    assert winner["version"] == 1
    # Exactly the winning delta is stored — the refused one left no trace.
    nodes = (await client.get("/v1/plane/nodes")).json()
    desired = next(n for n in nodes if n["node_id"] == "n_sig")["desired"]
    assert desired["version"] == 1
    assert desired["state"] == winner["state"]
    assert len(desired["state"]) == 1, "the refused command must not be merged in"


def _hb(node: str, seq: int, level: str, applied: int = 0) -> dict:
    return {"run_id": f"{node}-hb", "events": [{
        "schema_version": "1.0", "seq": seq, "node_id": node, "kind": "heartbeat",
        "ts": "2026-01-01T00:00:00Z",
        "payload": {"applied_version": applied, "level": level},
    }]}


async def test_a_retried_batch_does_not_roll_the_level_back(
    client: httpx.AsyncClient,
) -> None:
    """Delivery is at-least-once: the adapter resends a batch whenever its ack
    is lost, under the same Idempotency-Key. The telemetry route used to apply
    its side effects from the REQUEST's lines, so a resend re-applied an old
    heartbeat — a node that had degraded to LOCKED read NORMAL in the console
    again. That is the plane's one job told backwards."""
    await client.post("/v1/plane/n_rb/telemetry", json=_hb("n_rb", 0, "NORMAL"),
                      headers={"Idempotency-Key": "A"})
    await client.post("/v1/plane/n_rb/telemetry", json=_hb("n_rb", 1, "LOCKED"),
                      headers={"Idempotency-Key": "B"})

    resend = await client.post("/v1/plane/n_rb/telemetry",
                               json=_hb("n_rb", 0, "NORMAL"),
                               headers={"Idempotency-Key": "A"})
    assert resend.json()["stored"] == 0

    node = next(n for n in (await client.get("/v1/plane/nodes")).json()
                if n["node_id"] == "n_rb")
    assert node["reported"]["level"] == "LOCKED"


async def test_a_restarted_node_is_still_heard(client: httpx.AsyncClient) -> None:
    """A restarted adapter numbers its events from zero again. If it reuses a
    run id, the new heartbeats collide with the previous process's rows and
    store nothing — but they carry genuinely new state, so they must still be
    applied. Gating the side effects on "rows were written" instead of on the
    Idempotency-Key made a restarted node go permanently dark: reporting
    LOCKED while the console showed whatever it said before the restart."""
    await client.post("/v1/plane/n_rs/telemetry", json=_hb("n_rs", 0, "NORMAL"),
                      headers={"Idempotency-Key": "run1-0"})

    # Same run, same seq, new batch: the process restarted.
    restarted = await client.post("/v1/plane/n_rs/telemetry",
                                  json=_hb("n_rs", 0, "LOCKED", applied=5),
                                  headers={"Idempotency-Key": "run2-0"})
    assert restarted.json()["stored"] == 0, "the row really does collide"

    node = next(n for n in (await client.get("/v1/plane/nodes")).json()
                if n["node_id"] == "n_rs")
    assert node["reported"]["level"] == "LOCKED"
    assert node["reported"]["applied_version"] == 5


async def test_a_batch_that_climbs_and_falls_still_reports_the_peak(
    client: httpx.AsyncClient,
) -> None:
    """Reported state is LWW, so only the last heartbeat survives — but a batch
    that went NORMAL -> LOCKED -> NORMAL really did reach LOCKED, and the
    operator is entitled to be told even though nothing will remember it."""
    seen: list[dict] = []

    async def sink(url: str, body: dict) -> int:
        seen.append(body)
        return 200

    app = client._app  # type: ignore[attr-defined]
    app.state.notifier._post = sink  # noqa: SLF001
    app.state.notifier.subscribe("http://sink.test/h", ["level_transition_up"])

    await client.post("/v1/plane/n_pk/telemetry", json={
        "run_id": "n_pk-hb",
        "events": [
            {"schema_version": "1.0", "seq": i, "node_id": "n_pk",
             "kind": "heartbeat", "ts": "t",
             "payload": {"applied_version": 0, "level": level}}
            for i, level in enumerate(["NORMAL", "LOCKED", "NORMAL"])
        ],
    })

    node = next(n for n in (await client.get("/v1/plane/nodes")).json()
                if n["node_id"] == "n_pk")
    assert node["reported"]["level"] == "NORMAL", "LWW: the last one is stored"
    climbs = [b for b in seen if b.get("trigger") == "level_transition_up"]
    assert [(b["from"], b["to"]) for b in climbs] == [("NORMAL", "LOCKED")], \
        "the peak it passed through must page exactly once"


async def test_the_fleet_read_surfaces_do_not_scale_with_node_count(
    client: httpx.AsyncClient,
) -> None:
    """/v1/plane/nodes and /topology render EVERY node and the UI polls them.
    Built from the per-node getters they cost three round trips per node — 151
    SELECTs for 50 nodes. The count must not depend on how many nodes there
    are."""
    from sqlalchemy import event

    app = client._app  # type: ignore[attr-defined]
    counted = {"n": 0}

    @event.listens_for(app.state.store.engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, params, context, executemany) -> None:  # noqa: ANN001
        if statement.lstrip().upper().startswith("SELECT"):
            counted["n"] += 1

    async def cost(path: str) -> int:
        counted["n"] = 0
        await client.get(path)
        return counted["n"]

    for i in range(3):
        await client.post(f"/v1/plane/few{i}/telemetry", json=_hb(f"few{i}", 0, "NORMAL"))
    small = (await cost("/v1/plane/nodes"), await cost("/v1/plane/topology"))

    for i in range(30):
        await client.post(f"/v1/plane/many{i}/telemetry", json=_hb(f"many{i}", 0, "NORMAL"))
    large = (await cost("/v1/plane/nodes"), await cost("/v1/plane/topology"))

    assert small == large, f"query count grew with the fleet: {small} -> {large}"
