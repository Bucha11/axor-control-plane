"""Phase 6 backend: notifications (section 16), EvidenceCase share/export
(section 8.3), and the EE offline license check (monetization section 4)."""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.notifications import Notifier


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


# ── Notifier unit ─────────────────────────────────────────────────────────────

async def test_notifier_retries_then_dead_letters() -> None:
    calls = {"n": 0}

    async def failing_post(url: str, body: dict) -> int:
        calls["n"] += 1
        return 500

    n = Notifier(post=failing_post, max_attempts=3)
    n.subscribe("http://sink.test/hook", ["evidence_run"])
    delivered = await n.emit("evidence_run", "node1", {"cases": 1})
    assert delivered == 1
    assert calls["n"] == 3  # retried up to max_attempts
    assert len(n.dead_letters) == 1
    assert n.dead_letters[0].error == "status 500"


async def test_notifier_debounce_suppresses_repeats() -> None:
    seen = []

    async def ok_post(url: str, body: dict) -> int:
        seen.append(body)
        return 200

    n = Notifier(post=ok_post)
    n.subscribe("http://sink.test", ["level_transition_up"], debounce_seconds=5.0)
    assert await n.emit("level_transition_up", "n1", {"to": "LOCKED"}) == 1
    assert await n.emit("level_transition_up", "n1", {"to": "LOCKED"}) == 0  # debounced
    n.tick(6.0)
    assert await n.emit("level_transition_up", "n1", {"to": "LOCKED"}) == 1
    assert len(seen) == 2


async def test_unknown_trigger_rejected() -> None:
    n = Notifier()
    with pytest.raises(ValueError):
        n.subscribe("http://x", ["not_a_trigger"])


# ── level-transition-up over the telemetry path ───────────────────────────────

async def test_level_transition_up_emits_notification(
    client: httpx.AsyncClient,
) -> None:
    fired = []

    async def capture(url: str, body: dict) -> int:
        fired.append(body)
        return 200

    client._app.state.notifier._post = capture  # type: ignore[attr-defined]
    await client.post("/v1/notifications/subscribe", json={
        "url": "http://sink.test", "triggers": ["level_transition_up"],
    })

    def hb(level: str, seq: int) -> dict:
        return {"run_id": "r1", "events": [{
            "schema_version": "1.0", "seq": seq, "node_id": "n1",
            "kind": "heartbeat", "ts": "t", "causal_root": None,
            "gate": None, "verdict": None,
            "payload": {"applied_version": 1, "level": level},
        }]}

    await client.post("/v1/plane/n1/telemetry", json=hb("NORMAL", 0),
                      headers={"Idempotency-Key": "a"})
    await client.post("/v1/plane/n1/telemetry", json=hb("RESTRICTED", 1),
                      headers={"Idempotency-Key": "b"})
    assert len(fired) == 1
    assert fired[0]["to"] == "RESTRICTED" and fired[0]["from"] == "NORMAL"


# ── heat_threshold over the fact path ─────────────────────────────────────────

async def test_heat_crossing_fact_emits_heat_threshold(
    client: httpx.AsyncClient,
) -> None:
    fired = []

    async def capture(url: str, body: dict) -> int:
        fired.append(body)
        return 200

    client._app.state.notifier._post = capture  # type: ignore[attr-defined]
    await client.post("/v1/notifications/subscribe", json={
        "url": "http://sink.test", "triggers": ["heat_threshold"],
    })

    def fact(fid: str, score: float) -> dict:
        return {"fact": {
            "fact_id": fid, "fact_type": "heat_crossing",
            "score": score, "threshold": 0.8, "resource_id": "res_1",
        }}

    # Below threshold → no notification.
    await client.post("/v1/plane/n1/facts", json=fact("f1", 0.5))
    assert fired == []
    # At/over threshold → fires once.
    await client.post("/v1/plane/n1/facts", json=fact("f2", 0.9))
    assert len(fired) == 1
    assert fired[0]["trigger"] == "heat_threshold"
    assert fired[0]["score"] == 0.9 and fired[0]["resource_id"] == "res_1"


# ── cascade stop over parent/child topology (spec §12) ────────────────────────

async def test_cascade_stop_stops_the_whole_subtree(
    client: httpx.AsyncClient,
) -> None:
    # Build a small topology via desired-state `parent` fields: root → child →
    # grandchild, plus an unrelated node that must NOT be stopped.
    async def cmd(node: str, state: dict) -> None:
        r = await client.post(f"/v1/plane/{node}/command", json={
            "version": 1, "state": state, "operator": "op_ui",
            "timestamp": "", "sig": "",
        })
        assert r.status_code == 202, r.text

    await cmd("root", {"note": "root"})
    await cmd("child", {"parent": "root"})
    await cmd("grand", {"parent": "child"})
    await cmd("other", {"note": "unrelated"})

    r = await client.post("/v1/plane/root/cascade-stop")
    assert r.status_code == 202
    stopped = set(r.json()["stopped"])
    assert stopped == {"root", "child", "grand"}

    for node in ("root", "child", "grand"):
        nodes = await client.get("/v1/plane/nodes")
        info = next(n for n in nodes.json() if n["node_id"] == node)
        assert info["desired"]["state"]["stopped"] is True
    other = next(n for n in (await client.get("/v1/plane/nodes")).json()
                 if n["node_id"] == "other")
    assert other["desired"]["state"].get("stopped") is not True


# ── evidence auto-pin ─────────────────────────────────────────────────────────

async def test_set_evidence_auto_pins_deviation_to_must_block(
    client: httpx.AsyncClient,
) -> None:
    store = client._app.state.store  # type: ignore[attr-defined]
    await client.post("/v1/ingest/run_x", json={"node_id": "n1", "events": []})
    # Evidence with a deviation → auto-pinned to the must_block corpus side.
    await client.post("/v1/runs/run_x/evidence", json={
        "node_id": "n1", "scenario": "prompt_injection",
        "evidence": [{"case_id": "c1", "deviation": "exfil attempt"}],
    })
    pins = await store.pinned()
    assert any(p["run_id"] == "run_x" and p["side"] == "must_block" for p in pins)

    # Evidence with NO deviation → not pinned.
    await client.post("/v1/ingest/run_y", json={"node_id": "n1", "events": []})
    await client.post("/v1/runs/run_y/evidence", json={
        "node_id": "n1", "evidence": [{"case_id": "c2"}],
    })
    pins = await store.pinned()
    assert not any(p["run_id"] == "run_y" for p in pins)


# ── node_stale sweep ──────────────────────────────────────────────────────────

async def test_stale_sweep_fires_once_per_stale_episode() -> None:
    from datetime import UTC, datetime, timedelta

    from axor_backend.broadcast import Broadcast
    from axor_backend.monitor import stale_sweep

    fired = []

    async def capture(url: str, body: dict) -> int:
        fired.append(body)
        return 200

    class FakeStore:
        def __init__(self) -> None:
            self.rows: list[dict] = []

        async def list_reported(self) -> list[dict]:
            return list(self.rows)

    store = FakeStore()
    notifier = Notifier(post=capture)
    notifier.subscribe("http://sink.test", ["node_stale"])
    broadcast = Broadcast()
    now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=UTC)

    # Fresh node → not stale.
    store.rows = [{"node_id": "n1", "level": "NORMAL",
                   "updated_ts": (now - timedelta(seconds=5)).isoformat()}]
    seen: set[str] = set()
    assert await stale_sweep(store, notifier, broadcast, 30.0, seen, now) == 0

    # Silent past 3T → fires once, and stays quiet on the next sweep (edge).
    store.rows = [{"node_id": "n1", "level": "NORMAL",
                   "updated_ts": (now - timedelta(seconds=40)).isoformat()}]
    assert await stale_sweep(store, notifier, broadcast, 30.0, seen, now) == 1
    assert await stale_sweep(store, notifier, broadcast, 30.0, seen, now) == 0
    assert len(fired) == 1 and fired[0]["trigger"] == "node_stale"

    # Heartbeats again (fresh), then goes silent → re-arms and fires anew.
    store.rows = [{"node_id": "n1", "level": "NORMAL",
                   "updated_ts": (now - timedelta(seconds=1)).isoformat()}]
    assert await stale_sweep(store, notifier, broadcast, 30.0, seen, now) == 0
    store.rows = [{"node_id": "n1", "level": "NORMAL",
                   "updated_ts": (now - timedelta(seconds=40)).isoformat()}]
    assert await stale_sweep(store, notifier, broadcast, 30.0, seen, now) == 1
    assert len(fired) == 2


# ── share + export ────────────────────────────────────────────────────────────

async def test_share_link_is_revocable_and_scrubs_bodies(
    client: httpx.AsyncClient,
) -> None:
    await client.post("/v1/ingest/run_e", json={"node_id": "n1", "events": [
        {"schema_version": "1.0", "seq": 0, "node_id": "n1", "kind": "claim",
         "ts": "t", "causal_root": None, "gate": None, "verdict": None,
         "payload": {}},
    ]})
    await client.post("/v1/runs/run_e/evidence", json={"node_id": "n1", "evidence": [{
        "scenario": "demo", "deviation": "fabricated_tool_result",
        "verdict_source": "deterministic", "confidence": 1.0,
        "observed_reality": {"tool": "web_search", "response_body": "SECRET-LEAK"},
        "agent_claim": "web_search succeeded", "fault_attribution": [],
    }]})

    share = (await client.post("/v1/runs/run_e/cases/0/share")).json()
    token = share["token"]
    page = await client.get(f"/v1/share/{token}")
    assert page.status_code == 200
    assert "FABRICATED TOOL RESULT" in page.text
    assert "SECRET-LEAK" not in page.text  # raw bodies never exported (8.3)

    assert (await client.delete(f"/v1/share/{token}")).status_code == 200
    assert (await client.get(f"/v1/share/{token}")).status_code == 404


async def test_export_endpoint_renders_receipt(client: httpx.AsyncClient) -> None:
    await client.post("/v1/ingest/run_x2", json={"node_id": "n1", "events": [
        {"schema_version": "1.0", "seq": 0, "node_id": "n1", "kind": "claim",
         "ts": "t", "causal_root": None, "gate": None, "verdict": None,
         "payload": {}},
    ]})
    await client.post("/v1/runs/run_x2/evidence", json={"evidence": [{
        "scenario": "demo", "deviation": "corrupted_retrieval_used",
        "verdict_source": "deterministic", "confidence": 1.0,
        "observed_reality": {"canary": "AXOR_CANARY_x"}, "agent_claim": "used it",
        "fault_attribution": [{"fault_mode": "corrupt_retrieval",
                               "tool_name": "web_search", "influence": "strong"}],
    }]})
    page = await client.get("/v1/runs/run_x2/cases/0/export")
    assert page.status_code == 200
    assert "CORRUPTED RETRIEVAL USED" in page.text
    assert "corrupt_retrieval on web_search" in page.text


# ── EE license ────────────────────────────────────────────────────────────────

def _vendor_keypair() -> tuple[str, str]:
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    return bytes(key).hex(), key.verify_key.encode().hex()


async def test_valid_license_verifies_offline(client: httpx.AsyncClient) -> None:
    from axor_backend.ee.license import sign_license

    priv, pub = _vendor_keypair()
    lic = {"org": "Acme", "tier": "enterprise", "node_ceiling": 50,
           "expiry": "2027-01-01", "features": ["fleet_view", "compliance_reports"]}
    license_json = sign_license(lic, priv)
    resp = await client.post("/v1/license/verify", json={
        "license_json": license_json, "vendor_pubkey": pub,
    })
    assert resp.status_code == 200
    assert resp.json()["org"] == "Acme"
    assert "fleet_view" in resp.json()["features"]


async def test_tampered_license_rejected(client: httpx.AsyncClient) -> None:
    from axor_backend.ee.license import sign_license

    priv, pub = _vendor_keypair()
    lic = {"org": "Acme", "tier": "team", "node_ceiling": 5,
           "expiry": "2027-01-01", "features": []}
    license_json = sign_license(lic, priv)
    tampered = license_json.replace('"node_ceiling": 5', '"node_ceiling": 9999')
    resp = await client.post("/v1/license/verify", json={
        "license_json": tampered, "vendor_pubkey": pub,
    })
    assert resp.status_code == 403


def test_license_expiry_degrades_to_readonly() -> None:
    from axor_backend.ee.license import License

    lic = License(org="A", tier="team", node_ceiling=5, expiry="2026-01-01",
                  features=("fleet_view",))
    assert lic.enables("fleet_view", today="2025-06-01") is True
    assert lic.enables("fleet_view", today="2026-06-01") is False  # expired
    assert lic.is_expired("2026-06-01") is True
