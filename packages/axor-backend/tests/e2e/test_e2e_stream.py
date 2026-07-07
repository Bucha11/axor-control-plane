"""The live audit stream (spec §8) over the wire: a real text/event-stream the
browser reads with EventSource. In-process ASGITransport can't exercise a
long-lived SSE response; a real server can.
"""
from __future__ import annotations

import httpx
import pytest

from .conftest import read_sse

pytestmark = pytest.mark.e2e


async def test_audit_stream_replays_recorded_events(http: httpx.AsyncClient) -> None:
    run = "run_stream_e2e"
    events = [
        {"schema_version": "1.0", "seq": 0, "node_id": "n", "kind": "tool_call",
         "ts": "t", "causal_root": None, "gate": None, "verdict": "pass",
         "payload": {"tool": "email_read"}},
        {"schema_version": "1.0", "seq": 1, "node_id": "n", "kind": "claim",
         "ts": "t", "causal_root": None, "gate": None, "verdict": None, "payload": {}},
    ]
    r = await http.post(f"/v1/ingest/{run}", json={"node_id": "n", "events": events})
    assert r.status_code == 202

    # The stream replays the recorded trace as colour-coded `event:` frames.
    first = await read_sse(http, f"/v1/runs/{run}/stream", want_event="event", timeout=10.0)
    assert first["seq"] == 0
    assert first["kind"] == "tool_call"
