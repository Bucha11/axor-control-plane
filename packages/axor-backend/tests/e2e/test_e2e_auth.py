"""Auth enforcement against a real secured server (architecture §9): the gate is
opt-in, the master token is all-scope, minted keys are least-privilege, and the
SSE endpoints honour ?token= (EventSource can't set headers).
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from .conftest import Backend

pytestmark = pytest.mark.e2e

TOKEN = "master-secret-e2e"


@pytest.fixture
def secured(tmp_path: Path) -> Iterator[Backend]:
    b = Backend(
        tmp_path / "secured.db", tmp_path / "secured.log",
        {"AXOR_API_TOKEN": TOKEN, "AXOR_ALLOW_UNSIGNED": "1"},
    ).start()
    yield b
    b.stop()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_gate_blocks_then_admits(secured: Backend) -> None:
    async with httpx.AsyncClient(base_url=secured.url, timeout=10.0) as c:
        # status + healthz stay open; data endpoints are gated.
        status = (await c.get("/v1/auth/status")).json()
        assert status["auth_enabled"] is True and status["authenticated"] is False
        assert (await c.get("/v1/runs")).status_code == 401
        assert (await c.get("/v1/runs", headers=_bearer("wrong"))).status_code == 401
        assert (await c.get("/v1/runs", headers=_bearer(TOKEN))).status_code == 200


async def test_scoped_key_is_least_privilege_over_the_wire(secured: Backend) -> None:
    async with httpx.AsyncClient(base_url=secured.url, timeout=10.0) as c:
        # Minting needs admin (the master token).
        assert (await c.post("/v1/keys", json={"scopes": ["ingest"]})).status_code == 401
        key = (await c.post(
            "/v1/keys", json={"scopes": ["ingest"], "label": "proxy"},
            headers=_bearer(TOKEN),
        )).json()["secret"]

        # The ingest key may ingest…
        assert (await c.post(
            "/v1/ingest/run_authk", json={"node_id": "n", "events": []},
            headers=_bearer(key),
        )).status_code == 202
        # …but not operate the plane.
        cmd = await c.post(
            "/v1/plane/n/command",
            json={"version": 1, "state": {"paused": True}, "operator": "op",
                  "timestamp": "t", "sig": ""},
            headers=_bearer(key),
        )
        assert cmd.status_code == 403 and cmd.json()["need"] == "operate"


async def test_a_query_token_is_refused_on_the_json_event_log(
    secured: Backend,
) -> None:
    async with httpx.AsyncClient(base_url=secured.url, timeout=10.0) as c:
        await c.post("/v1/ingest/run_authsse", json={"node_id": "n", "events": [
            {"schema_version": "1.0", "seq": 0, "node_id": "n", "kind": "claim",
             "ts": "t", "causal_root": None, "gate": None, "verdict": None, "payload": {}},
        ]}, headers=_bearer(TOKEN))
        # `/events` is ordinary JSON, not a stream: a URL token buys nothing
        # there but exposure, so it is refused like the rest of the API.
        assert (await c.get("/v1/runs/run_authsse/events")).status_code == 401
        assert (
            await c.get(f"/v1/runs/run_authsse/events?token={TOKEN}")
        ).status_code == 401
