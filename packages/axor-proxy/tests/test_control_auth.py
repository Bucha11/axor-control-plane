"""The proxy's control surface can require a bearer (audit F-17).

The proxy arms runs, injects faults into live tool traffic, spawns governed
nodes and reads back recorded traces — and docker-compose publishes its port.
It had no authentication of any kind. A token is opt-in so a loopback bench is
unchanged; set it and every /axor route needs the bearer.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_proxy.app import ProxyState, create_app

TOKEN = "proxy-secret"


def _state(tmp_path: pathlib.Path, token: str | None) -> ProxyState:
    return ProxyState(tools={}, trace_dir=tmp_path / "traces",
                      control_token=token)


@pytest.fixture
async def guarded(tmp_path: pathlib.Path):  # noqa: ANN201
    app = create_app(_state(tmp_path, TOKEN))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://proxy.test") as c:
        yield c


@pytest.fixture
async def open_proxy(tmp_path: pathlib.Path):  # noqa: ANN201
    app = create_app(_state(tmp_path, None))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://proxy.test") as c:
        yield c


async def test_control_routes_need_the_token(guarded: httpx.AsyncClient) -> None:
    arm = await guarded.post("/axor/runs", json={"scenario": "s", "faults": []})
    assert arm.status_code == 401
    assert "AXOR_PROXY_TOKEN" in arm.json()["detail"]

    ok = await guarded.post("/axor/runs", json={"scenario": "s", "faults": []},
                            headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 201
    run_id = ok.json()["run_id"]

    assert (await guarded.get(f"/axor/runs/{run_id}")).status_code == 401
    assert (await guarded.get(
        f"/axor/runs/{run_id}",
        headers={"Authorization": f"Bearer {TOKEN}"})).status_code == 200


async def test_a_wrong_token_is_not_a_near_miss(guarded: httpx.AsyncClient) -> None:
    for header in ({"Authorization": f"Bearer {TOKEN}x"},
                   {"Authorization": TOKEN},  # not a bearer
                   {"Authorization": "Bearer "}):
        r = await guarded.post("/axor/runs", json={"scenario": "s", "faults": []},
                               headers=header)
        assert r.status_code == 401


async def test_healthz_stays_open(guarded: httpx.AsyncClient) -> None:
    """A container health check has no credential to present, and liveness
    plus 'is a run armed' is not worth gating."""
    assert (await guarded.get("/axor/healthz")).status_code == 200


async def test_no_token_keeps_the_historical_open_posture(
    open_proxy: httpx.AsyncClient,
) -> None:
    assert (await open_proxy.post(
        "/axor/runs", json={"scenario": "s", "faults": []})).status_code == 201
