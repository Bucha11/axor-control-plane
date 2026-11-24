"""A link already sent can be found and revoked, and handing one out — or
taking it back — is on the audit trail. After a reload "Share" minted a NEW
link and "revoke" burned that one, while the link already sent kept opening."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def test_the_link_already_sent_is_listed_and_can_be_revoked(client: httpx.AsyncClient) -> None:
    await client.post("/v1/demo/seed-adapter-runs")
    run = next(r for r in (await client.get("/v1/runs")).json() if r["evidence"])
    run_id = run["run_id"]
    sent = (await client.post(f"/v1/runs/{run_id}/cases/0/share")).json()
    later = (await client.post(f"/v1/runs/{run_id}/cases/0/share")).json()   # after a reload

    links = (await client.get("/v1/share-links", params={"run_id": run_id})).json()["links"]
    assert {ln["token"] for ln in links} == {sent["token"], later["token"]}
    assert all(not ln["revoked"] for ln in links)

    assert (await client.delete(f"/v1/share/{sent['token']}")).status_code == 200
    assert (await client.get(sent["url"])).status_code == 404
    links = {ln["token"]: ln for ln in (await client.get("/v1/share-links")).json()["links"]}
    assert links[sent["token"]]["revoked"] and not links[later["token"]]["revoked"]

    acts = [i["action"] for i in (await client.get("/v1/audit")).json()["items"]]
    assert acts.count("share_made") == 2 and acts.count("share_revoked") == 1


def test_the_list_is_an_operators() -> None:
    from axor_backend.auth import required_scope
    assert required_scope("GET", "/v1/share-links") == "operate"
