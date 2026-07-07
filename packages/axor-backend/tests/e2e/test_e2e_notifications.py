"""Notifications end to end: the backend actually POSTs a webhook to an external
sink when a node's degradation crosses upward (spec §16). Outbound delivery is
something only a real process talking to a real listener can prove.
"""
from __future__ import annotations

import httpx
import pytest

from .conftest import WebhookSink

pytestmark = pytest.mark.e2e


async def test_level_transition_delivers_a_webhook(
    http: httpx.AsyncClient, webhook: WebhookSink,
) -> None:
    node = "gov-notify"
    sub = await http.post("/v1/notifications/subscribe", json={
        "url": webhook.url, "triggers": ["level_transition_up"],
    })
    assert sub.status_code == 200

    # A first heartbeat at CAUTIOUS is an upward transition from the NORMAL
    # default → the backend fans a webhook out to the sink.
    await http.post(f"/v1/plane/{node}/telemetry", json={
        "run_id": f"{node}-hb",
        "events": [{
            "seq": 0, "kind": "heartbeat", "node_id": node, "ts": "t",
            "payload": {"applied_version": 0, "level": "CAUTIOUS", "budget_remaining": None},
        }],
    })

    msg = webhook.wait_for(
        lambda m: m.get("trigger") == "level_transition_up" and m.get("node_id") == node,
        timeout=10.0,
    )
    assert msg["to"] == "CAUTIOUS"
