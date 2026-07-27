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


async def test_notifier_hands_dead_letters_to_the_persistence_sink() -> None:
    persisted = []

    async def failing_post(url: str, body: dict) -> int:
        return 503

    async def sink(letter) -> None:  # noqa: ANN001 - DeadLetter
        persisted.append(letter)

    n = Notifier(post=failing_post, max_attempts=2, dead_sink=sink)
    n.subscribe("http://sink.test/hook", ["node_stale"])
    await n.emit("node_stale", "node1", {"silent_for": 31})
    assert len(persisted) == 1
    assert persisted[0].payload["trigger"] == "node_stale"


async def test_notifier_survives_a_broken_persistence_sink() -> None:
    async def failing_post(url: str, body: dict) -> int:
        return 500

    async def broken_sink(letter) -> None:  # noqa: ANN001
        raise RuntimeError("db down")

    n = Notifier(post=failing_post, max_attempts=1, dead_sink=broken_sink)
    n.subscribe("http://sink.test/hook", ["node_stale"])
    # Must not raise — the in-memory record still lands.
    assert await n.emit("node_stale", "node1", {}) == 1
    assert len(n.dead_letters) == 1


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


async def test_share_links_and_subscriptions_survive_a_restart(
    tmp_path: pathlib.Path,
) -> None:
    """Share links and notification subscriptions are primary data — a backend
    restart must not 404 a live permalink or silently stop notifications. Two
    app instances over one DB file stand in for the restart."""
    db = f"sqlite+aiosqlite:///{tmp_path}/axor.db"

    def app() -> object:  # a fresh instance, same DB — i.e. a restart
        return create_app(database_url=db, operator_keys={}, allow_unsigned=True)

    a = app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=a), base_url="http://backend.test"
    ) as c, a.router.lifespan_context(a):
        await c.post("/v1/ingest/run_s", json={"node_id": "n1", "events": [
            {"schema_version": "1.0", "seq": 0, "node_id": "n1", "kind": "claim",
             "ts": "t", "causal_root": None, "gate": None, "verdict": None,
             "payload": {}},
        ]})
        await c.post("/v1/runs/run_s/evidence", json={"evidence": [{
            "scenario": "demo", "deviation": "fabricated_tool_result",
            "verdict_source": "deterministic", "confidence": 1.0,
            "observed_reality": {"tool": "web_search"},
            "agent_claim": "ok", "fault_attribution": [],
        }]})
        token = (await c.post("/v1/runs/run_s/cases/0/share")).json()["token"]
        await c.post("/v1/notifications/subscribe", json={
            "url": "http://sink.test/hook", "triggers": ["evidence_run"],
        })

    # Restart: brand-new app, same DB. The link still resolves and the
    # subscription is back in the notifier (not just a dead DB row).
    b = app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=b), base_url="http://backend.test"
    ) as c, b.router.lifespan_context(b):
        assert (await c.get(f"/v1/share/{token}")).status_code == 200
        subs = b.state.notifier._subs  # type: ignore[attr-defined]
        assert [s.url for s in subs] == ["http://sink.test/hook"]

        # A revoke also persists across the next restart.
        assert (await c.delete(f"/v1/share/{token}")).status_code == 200

    d = app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=d), base_url="http://backend.test"
    ) as c, d.router.lifespan_context(d):
        assert (await c.get(f"/v1/share/{token}")).status_code == 404
        # Rehydrate is idempotent: the one subscription is not duplicated.
        assert len(d.state.notifier._subs) == 1  # type: ignore[attr-defined]


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

    # PDF variant: a valid, self-contained PDF byte stream (spec §8.3).
    pdf = await client.get("/v1/runs/run_x2/cases/0/export", params={"format": "pdf"})
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-1.4")
    assert pdf.content.rstrip().endswith(b"%%EOF")
    assert b"AXOR EVIDENCECASE" in pdf.content


# ── EE license ────────────────────────────────────────────────────────────────

def _vendor_keypair() -> tuple[str, str]:
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    return bytes(key).hex(), key.verify_key.encode().hex()


async def test_valid_license_verifies_offline(client: httpx.AsyncClient) -> None:
    from axor_backend.ee.license import sign_license

    priv, pub = _vendor_keypair()
    # Enterprise Platform = Security workspace + both modules + self-hosted
    # (axor-packaging.md §5); it is a security workspace_tier, not its own tier.
    lic = {"organization": "Acme", "workspace_tier": "security",
           "modules": {"private_lab": True, "control_plane": True},
           "governed_node_ceiling": 50, "self_hosted_runner": True,
           "expires_at": "2027-01-01", "features": ["sso", "compliance_exports"]}
    license_json = sign_license(lic, priv)
    resp = await client.post("/v1/license/verify", json={
        "license_json": license_json, "vendor_pubkey": pub,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["organization"] == "Acme"
    assert body["workspace_tier"] == "security"
    assert body["modules"] == {"private_lab": True, "control_plane": True}
    assert body["governed_node_ceiling"] == 50
    assert "sso" in body["features"]


async def test_tampered_license_rejected(client: httpx.AsyncClient) -> None:
    from axor_backend.ee.license import sign_license

    priv, pub = _vendor_keypair()
    lic = {"organization": "Acme", "workspace_tier": "team",
           "modules": {"private_lab": True, "control_plane": False},
           "governed_node_ceiling": 5, "self_hosted_runner": False,
           "expires_at": "2027-01-01", "features": []}
    license_json = sign_license(lic, priv)
    tampered = license_json.replace('"governed_node_ceiling": 5',
                                    '"governed_node_ceiling": 9999')
    resp = await client.post("/v1/license/verify", json={
        "license_json": tampered, "vendor_pubkey": pub,
    })
    assert resp.status_code == 403


def test_license_expiry_degrades_to_readonly() -> None:
    from axor_backend.ee.license import License

    lic = License(organization="A", workspace_tier="team", modules=(),
                  governed_node_ceiling=5, expires_at="2026-01-01",
                  features=("sso",))
    assert lic.enables("sso", today="2025-06-01") is True
    assert lic.enables("sso", today="2026-06-01") is False  # expired
    assert lic.is_expired("2026-06-01") is True


async def test_license_verify_reports_node_ceiling_telemetry(
    client: httpx.AsyncClient,
) -> None:
    """§5: verify returns live_nodes/over_ceiling — a warning, never a block."""
    from axor_backend.ee.license import sign_license

    priv, pub = _vendor_keypair()
    lic = sign_license({"organization": "A", "workspace_tier": "team",
                        "modules": {"private_lab": True, "control_plane": True},
                        "governed_node_ceiling": 1, "self_hosted_runner": False,
                        "expires_at": "2999-01-01", "features": []}, priv)
    # Two live nodes vs a ceiling of 1.
    for node in ("ce_n1", "ce_n2"):
        await client.post(f"/v1/plane/{node}/telemetry", json={
            "run_id": f"{node}-hb",
            "events": [{"seq": 0, "kind": "heartbeat",
                        "payload": {"applied_version": 0, "level": "NORMAL"}}],
        })
    r = (await client.post("/v1/license/verify", json={
        "license_json": lic, "vendor_pubkey": pub,
    })).json()
    assert r["live_nodes"] >= 2 and r["over_ceiling"] is True


def test_license_cli_roundtrip(tmp_path, capsys) -> None:  # noqa: ANN001
    import json as _json

    from axor_backend.ee.cli import main

    assert main(["keygen"]) == 0
    keys = _json.loads(capsys.readouterr().out)
    assert main(["issue", "--key", keys["vendor_private_key"], "--org", "T",
                 "--expires-at", "2999-01-01"]) == 0
    lic_file = tmp_path / "l.json"
    lic_file.write_text(capsys.readouterr().out)
    assert main(["verify", "--pubkey", keys["vendor_public_key"],
                 str(lic_file)]) == 0
    assert "VALID" in capsys.readouterr().out


def test_license_cli_reads_key_from_file_and_env(
    tmp_path, capsys, monkeypatch,  # noqa: ANN001
) -> None:
    """The private key should not have to travel via argv (shell history):
    --key-file and AXOR_VENDOR_KEY both work; no key at all is a clear error."""
    import json as _json

    from axor_backend.ee.cli import main

    assert main(["keygen"]) == 0
    keys = _json.loads(capsys.readouterr().out)

    key_file = tmp_path / "vendor.key"
    key_file.write_text(keys["vendor_private_key"] + "\n")
    assert main(["issue", "--key-file", str(key_file), "--org", "F",
                 "--expires-at", "2999-01-01"]) == 0
    assert '"organization"' in capsys.readouterr().out

    monkeypatch.setenv("AXOR_VENDOR_KEY", keys["vendor_private_key"])
    assert main(["issue", "--org", "E", "--expires-at", "2999-01-01"]) == 0
    assert '"organization"' in capsys.readouterr().out

    monkeypatch.delenv("AXOR_VENDOR_KEY")
    assert main(["issue", "--org", "N", "--expires-at", "2999-01-01"]) == 2
    assert "no signing key" in capsys.readouterr().err
