"""The tool policy is the workspace's, not one browser's: another person opening
Policy saw it rebuilt from the agent's runs — a refund tool as "reads", the
ticket's outside-text field and the email's deciding argument gone."""
from __future__ import annotations

import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from axor_backend.app import create_app

POLICY = {
    "read_ticket": {"klass": "READ", "outside": True, "outsideFields": ["text"],
                    "url": "http://tools/tickets"},
    "issue_refund": {"klass": "WRITE", "outside": False},
    "send_email": {"klass": "EXPORT", "outside": False, "driving": ["to"]},
}


@pytest.fixture
async def client(tmp_path: pathlib.Path) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db",
                     operator_keys={}, allow_unsigned=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


async def test_the_policy_is_kept_for_the_workspace_and_audited(client: httpx.AsyncClient) -> None:
    assert (await client.get("/v1/policy")).json() == {"tools": {}, "updated_ts": None,
                                                       "version": 0}
    r = await client.put("/v1/policy", json={"tools": POLICY})
    assert r.status_code == 200 and r.json()["changed"] == sorted(POLICY)
    got = (await client.get("/v1/policy")).json()["tools"]
    assert got["read_ticket"]["outsideFields"] == ["text"]
    assert got["send_email"]["driving"] == ["to"]
    assert got["issue_refund"]["klass"] == "WRITE"
    # saving the same policy again is not another act
    assert (await client.put("/v1/policy", json={"tools": POLICY})).json()["changed"] == []
    acts = [i for i in (await client.get("/v1/audit")).json()["items"]
            if i["action"] == "policy_saved"]
    assert len(acts) == 1


async def test_a_typo_is_a_400_naming_the_field(client: httpx.AsyncClient) -> None:
    r = await client.put("/v1/policy", json={"tools": {"x": {"klass": "export"}}})
    assert r.status_code == 400 and "klass" in r.text
    r = await client.put("/v1/policy", json={"tools": {"x": {"klass": "READ", "driving": "to"}}})
    assert r.status_code == 400 and "driving" in r.text


def test_writing_it_is_an_operators_act() -> None:
    from axor_backend.auth import required_scope
    assert required_scope("PUT", "/v1/policy") == "operate"


async def test_a_save_from_a_stale_screen_is_refused_naming_what_changed(
        client: httpx.AsyncClient) -> None:
    """QA-212: two tabs read the policy; the one that saved second put back
    whole what it had read, undoing the other's change, and was told saved."""
    v0 = (await client.get("/v1/policy")).json()["version"]
    r = await client.put("/v1/policy", json={"tools": POLICY, "version": v0})
    assert r.status_code == 200
    v1 = r.json()["version"]
    assert v1 == v0 + 1 == (await client.get("/v1/policy")).json()["version"]
    # tab A makes the refund tool an export; tab B still holds v1
    a = {**POLICY, "issue_refund": {"klass": "EXPORT", "outside": False}}
    r = await client.put("/v1/policy", json={"tools": a, "version": v1})
    assert r.status_code == 200
    b = {**POLICY, "send_email": {"klass": "EXPORT", "outside": True}}
    r = await client.put("/v1/policy", json={"tools": b, "version": v1})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "stale" and detail["changed"] == ["issue_refund"]
    assert "issue_refund" in detail["detail"] and "reload" in detail["detail"]
    got = (await client.get("/v1/policy")).json()
    assert got["tools"]["issue_refund"]["klass"] == "EXPORT"
    assert got["tools"]["send_email"]["outside"] is False
    # read again, B's change goes through
    r = await client.put("/v1/policy", json={"tools": {**got["tools"], **{"send_email": b["send_email"]}},
                                             "version": got["version"]})
    assert r.status_code == 200
    # a caller that sends no version saves as before
    assert (await client.put("/v1/policy", json={"tools": POLICY})).status_code == 200
