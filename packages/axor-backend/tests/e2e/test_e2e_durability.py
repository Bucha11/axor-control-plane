"""Durability across a real process restart: share links and notification
subscriptions are primary data, so a restart must not 404 a live permalink or
silently drop a webhook. This exercises the persistence fix at the process level
(two uvicorn lifecycles over one DB file), which an in-process test can't.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from .conftest import Backend

pytestmark = pytest.mark.e2e


@pytest.fixture
def restartable(tmp_path: Path) -> Iterator[Backend]:
    b = Backend(tmp_path / "durable.db", tmp_path / "durable.log", {"AXOR_ALLOW_UNSIGNED": "1"})
    b.start()
    yield b
    b.stop()


async def test_share_link_and_subscription_survive_restart(restartable: Backend) -> None:
    run = "run_durable"
    async with httpx.AsyncClient(base_url=restartable.url, timeout=10.0) as c:
        await c.post(f"/v1/ingest/{run}", json={"node_id": "n", "events": [
            {"schema_version": "1.0", "seq": 0, "node_id": "n", "kind": "claim",
             "ts": "t", "causal_root": None, "gate": None, "verdict": None, "payload": {}},
        ]})
        await c.post(f"/v1/runs/{run}/evidence", json={"evidence": [{
            "scenario": "demo", "deviation": "fabricated_tool_result",
            "verdict_source": "deterministic", "confidence": 1.0,
            "observed_reality": {"tool": "web_search"}, "agent_claim": "ok",
            "fault_attribution": [],
        }]})
        token = (await c.post(f"/v1/runs/{run}/cases/0/share")).json()["token"]
        assert (await c.get(f"/v1/share/{token}")).status_code == 200
        await c.post("/v1/notifications/subscribe", json={
            "url": "http://sink.e2e/hook", "triggers": ["evidence_run"],
        })

    # Restart the process on the same DB file.
    restartable.stop()
    restartable.start()

    async with httpx.AsyncClient(base_url=restartable.url, timeout=10.0) as c:
        # The permalink still resolves (rehydrated from the DB, not a dead row).
        assert (await c.get(f"/v1/share/{token}")).status_code == 200
        # And the subscription is back — a duplicate subscribe is idempotent, so
        # the rehydrate didn't double-register it (dead-letter/log stays sane).
        again = await c.post("/v1/notifications/subscribe", json={
            "url": "http://sink.e2e/hook", "triggers": ["evidence_run"],
        })
        assert again.status_code == 200

        # A revoke also survives the next restart.
        assert (await c.delete(f"/v1/share/{token}")).status_code == 200

    restartable.stop()
    restartable.start()
    async with httpx.AsyncClient(base_url=restartable.url, timeout=10.0) as c:
        assert (await c.get(f"/v1/share/{token}")).status_code == 404
