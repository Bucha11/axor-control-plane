"""The workspace knows what it is for, so a teammate joining it does too."""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path):  # noqa: ANN201
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/w.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, app.router.lifespan_context(app):
        yield c


async def test_unset_it_follows_what_reports(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/workspace")).json() == {"journey": None}
    coding = {"kind": "coding", "agent": "claude-code", "developer": "dev-1"}
    assert (await client.post("/v1/plane/claude-code-a/profile", json=coding)).status_code < 300
    assert (await client.get("/v1/workspace")).json() == {"journey": "coding"}
    await client.post("/v1/plane/support-bot/profile", json={"kind": "agent"})
    assert (await client.get("/v1/workspace")).json() == {"journey": "both"}


async def test_once_chosen_it_is_the_workspaces(client: httpx.AsyncClient) -> None:
    r = await client.put("/v1/workspace/journey", json={"journey": "coding"})
    assert r.status_code == 200
    await client.post("/v1/plane/support-bot/profile", json={"kind": "agent"})
    assert (await client.get("/v1/workspace")).json() == {"journey": "coding"}
    assert (await client.put("/v1/workspace/journey", json={"journey": "x"})).status_code == 400
