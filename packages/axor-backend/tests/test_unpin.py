"""A case can be taken out of the check, and keeping or removing one is on the
audit trail. A pin nothing could be verified against used to hold every
release at "not safe" for good — there was no way to remove it."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def test_a_kept_case_can_be_taken_out_and_both_are_audited(client: httpx.AsyncClient) -> None:
    r = await client.post("/v1/pins/run_a", json={"side": "must_block", "label": "the attack"})
    assert r.status_code == 200
    assert (await client.get("/v1/pins")).json()["total"] == 1

    r = await client.delete("/v1/pins/run_a")
    assert r.status_code == 200
    assert (await client.get("/v1/pins")).json()["total"] == 0
    assert (await client.delete("/v1/pins/run_a")).status_code == 404

    acts = [i["action"] for i in (await client.get("/v1/audit")).json()["items"]]
    assert "pin_must_block" in acts and "unpin" in acts
