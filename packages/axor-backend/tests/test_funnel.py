"""The sign-up funnel: anonymous steps in, the operator's report out."""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import httpx
import pytest
from axor_backend.app import create_app

MASTER = "master-funnel"


@pytest.fixture
async def client(tmp_path: pathlib.Path, monkeypatch):  # noqa: ANN001, ANN201
    monkeypatch.setenv("AXOR_GUEST_SESSIONS", "1")
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/f.db",
                     operator_keys={}, allow_unsigned=True, api_token=MASTER)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, app.router.lifespan_context(app):
        yield c


def _step(event: str, tab: str, detail: str = "") -> dict:
    return {"event": event, "session_id": tab, "detail": detail}


async def test_steps_in_and_the_operator_reads_conversion(client: httpx.AsyncClient) -> None:
    for tab in ("tab-aaaaaaaa", "tab-bbbbbbbb", "tab-cccccccc", "tab-dddddddd"):
        assert (await client.post("/v1/funnel", json=_step("visit", tab))).status_code == 204
    for tab in ("tab-aaaaaaaa", "tab-bbbbbbbb"):
        await client.post("/v1/funnel", json=_step("guest_started", tab))
        await client.post("/v1/funnel", json=_step("lie_caught", tab))
    await client.post("/v1/funnel",
                      json=_step("signup_prompt_shown", "tab-aaaaaaaa", "share links"))
    await client.post("/v1/funnel", json=_step("signup_completed", "tab-aaaaaaaa"))

    # not a tenant's to read
    assert (await client.get("/v1/funnel")).status_code == 401
    guest = (await client.post("/v1/auth/guest")).json()["token"]
    as_guest = {"Authorization": f"Bearer {guest}"}
    assert (await client.get("/v1/funnel", headers=as_guest)).status_code == 403

    r = (await client.get("/v1/funnel", headers={"Authorization": f"Bearer {MASTER}"})).json()
    counts = {s["step"]: s["count"] for s in r["steps"]}
    assert counts["visited"] == 4
    assert counts["worked as a guest"] == 2
    assert counts["saw it work (demo fleet or a caught lie)"] == 2
    assert counts["signed up"] == 1
    assert r["steps"][4]["from_visit"] == 0.25
    assert r["signup_prompts"] == {"share links": 1}


async def test_only_known_steps_are_taken(client: httpx.AsyncClient) -> None:
    for bad in (_step("bought_a_yacht", "tab-aaaaaaaa"), _step("visit", "x"),
                _step("visit", "tab-aaaaaaaa", "<script>"),
                _step("agent_connected", "tab-aaaaaaaa")):
        assert (await client.post("/v1/funnel", json=bad)).status_code == 400, bad


async def test_a_real_agent_counts_once_and_a_guests_never(client: httpx.AsyncClient) -> None:
    line = {"schema_version": "1.0", "seq": 0, "node_id": "n1", "kind": "heartbeat",
            "ts": datetime.now(UTC).isoformat(), "causal_root": None, "gate": None,
            "verdict": None, "payload": {"applied_version": 0, "level": "NORMAL"}}
    master = {"Authorization": f"Bearer {MASTER}"}
    for _ in range(3):
        await client.post("/v1/plane/n1/telemetry", headers=master, json={"events": [line]})
    guest = {"Authorization": f"Bearer {(await client.post('/v1/auth/guest')).json()['token']}"}
    await client.post("/v1/plane/n2/telemetry", headers=guest, json={"events": [line]})
    r = (await client.get("/v1/funnel", headers=master)).json()
    assert r["agents_connected"] == 1
