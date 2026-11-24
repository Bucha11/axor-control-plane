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
    assert (await client.get("/v1/policy")).json() == {"tools": {}, "updated_ts": None}
    r = await client.put("/v1/policy", json={"tools": POLICY})
    assert r.status_code == 200 and r.json()["changed"] == sorted(POLICY)
    got = (await client.get("/v1/policy")).json()["tools"]
    assert got["read_ticket"]["outsideFields"] == ["text"]
    assert got["send_email"]["driving"] == ["to"]
    assert got["issue_refund"]["klass"] == "WRITE"
    # saving the same policy again is not another act
    assert (await client.put("/v1/policy", json={"tools": POLICY})).json()["changed"] == []
    acts = [i for i in (await client.get("/v1/audit")).json()["items"] if i["action"] == "policy_saved"]
    assert len(acts) == 1


async def test_a_typo_is_a_400_naming_the_field(client: httpx.AsyncClient) -> None:
    r = await client.put("/v1/policy", json={"tools": {"x": {"klass": "export"}}})
    assert r.status_code == 400 and "klass" in r.text
    r = await client.put("/v1/policy", json={"tools": {"x": {"klass": "READ", "driving": "to"}}})
    assert r.status_code == 400 and "driving" in r.text


def test_writing_it_is_an_operators_act() -> None:
    from axor_backend.auth import required_scope
    assert required_scope("PUT", "/v1/policy") == "operate"
