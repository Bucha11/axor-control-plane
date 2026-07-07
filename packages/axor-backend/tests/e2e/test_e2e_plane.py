"""Plane protocol over the wire: a governed node heartbeats and appears live, an
operator command is fanned out on the real desired SSE stream, and desired /
reported converge. The signed-command path runs against a backend booted with
operator keys (allow_unsigned off), exactly as a locked-down deployment.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from axor_backend.signing import signed_payload
from nacl.signing import SigningKey

from .conftest import Backend, read_sse

pytestmark = pytest.mark.e2e

OP = "op_e2e"


def _heartbeat(node: str, *, level: str = "NORMAL", applied: int = 0, run: str | None = None) -> dict:
    return {
        "run_id": run or f"{node}-hb",
        "events": [{
            "seq": 0, "kind": "heartbeat", "node_id": node, "ts": "t",
            "payload": {"applied_version": applied, "level": level, "budget_remaining": None},
        }],
    }


async def test_heartbeat_makes_a_node_live_and_state_converges(
    http: httpx.AsyncClient,
) -> None:
    node = "gov-converge"
    # First heartbeat: node appears in the topology, reported at v0.
    await http.post(f"/v1/plane/{node}/telemetry", json=_heartbeat(node))
    nodes = (await http.get("/v1/plane/nodes")).json()
    assert any(n["node_id"] == node for n in nodes)

    # Operator commands a pause (unsigned — open posture): desired advances to v1
    # while reported still lags at v0 (the honest divergence Control renders).
    cmd = await http.post(f"/v1/plane/{node}/command", json={
        "version": 1, "state": {"paused": True},
        "operator": OP, "timestamp": datetime.now(UTC).isoformat(), "sig": "",
    })
    assert cmd.status_code == 202 and cmd.json()["version"] == 1
    info = next(n for n in (await http.get("/v1/plane/nodes")).json() if n["node_id"] == node)
    assert info["desired"]["version"] == 1 and info["reported"]["applied_version"] == 0

    # The node applies it and heartbeats back: now converged.
    await http.post(f"/v1/plane/{node}/telemetry", json=_heartbeat(node, applied=1))
    info = next(n for n in (await http.get("/v1/plane/nodes")).json() if n["node_id"] == node)
    assert info["reported"]["applied_version"] == 1


async def test_desired_sse_delivers_the_command_delta_live(
    http: httpx.AsyncClient, backend,  # noqa: ANN001
) -> None:
    node = "gov-sse"
    # A second client sends the command while the first holds the SSE stream open,
    # so we observe a live delta (not just the snapshot).
    async def push() -> None:
        await asyncio.sleep(0.4)  # let the subscription attach first
        async with httpx.AsyncClient(base_url=backend.url, timeout=10.0) as c:
            r = await c.post(f"/v1/plane/{node}/command", json={
                "version": 1, "state": {"budget_cap_calls": 7},
                "operator": OP, "timestamp": datetime.now(UTC).isoformat(), "sig": "",
            })
            assert r.status_code == 202

    task = asyncio.create_task(push())
    delta = await read_sse(http, f"/v1/plane/{node}/desired", want_event="delta", timeout=10.0)
    await task
    assert delta["version"] == 1
    assert delta["state"]["budget_cap_calls"] == 7


async def test_cascade_stop_walks_the_subtree(http: httpx.AsyncClient) -> None:
    # parent ← child topology via each node's desired `parent`.
    for node, parent in [("casc-root", None), ("casc-child", "casc-root")]:
        state = {"parent": parent} if parent else {"alive": True}
        await http.post(f"/v1/plane/{node}/command", json={
            "version": 1, "state": state,
            "operator": OP, "timestamp": datetime.now(UTC).isoformat(), "sig": "",
        })
    out = (await http.post("/v1/plane/casc-root/cascade-stop")).json()
    assert set(out["stopped"]) >= {"casc-root", "casc-child"}


# ── signed posture (own backend with operator keys, allow_unsigned off) ─────────

@pytest.fixture
def signed_backend(tmp_path: Path) -> Iterator[Backend]:
    key = SigningKey(b"\x07" * 32)
    b = Backend(
        tmp_path / "signed.db", tmp_path / "signed.log",
        {"AXOR_OPERATOR_KEYS": f'{{"{OP}": "{key.verify_key.encode().hex()}"}}',
         "AXOR_ALLOW_UNSIGNED": "0"},
    ).start()
    b.signing_key = key  # type: ignore[attr-defined]
    yield b
    b.stop()


async def test_signed_command_verified_over_the_wire(signed_backend: Backend) -> None:
    key: SigningKey = signed_backend.signing_key  # type: ignore[attr-defined]
    node = "gov-signed"
    ts = datetime.now(UTC).isoformat()
    state = {"paused": True}
    good_sig = key.sign(signed_payload(node, 1, state, ts)).signature.hex()

    async with httpx.AsyncClient(base_url=signed_backend.url, timeout=10.0) as c:
        # An unsigned command is refused when operator keys are configured.
        bad = await c.post(f"/v1/plane/{node}/command", json={
            "version": 1, "state": state, "operator": OP, "timestamp": ts, "sig": "",
        })
        assert bad.status_code == 403
        # A correctly-signed command is accepted.
        ok = await c.post(f"/v1/plane/{node}/command", json={
            "version": 1, "state": state, "operator": OP, "timestamp": ts, "sig": good_sig,
        })
        assert ok.status_code == 202 and ok.json()["state"] == {"paused": True}
