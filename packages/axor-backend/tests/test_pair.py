"""Pair sessions: teammates on any plan; someone from outside the team only
when the pair was opened for them and the inviting workspace pays; notes reach
the agent through its sidecar, and the pair sees them land."""
from __future__ import annotations

import base64
import pathlib
import time
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
from axor_backend.app import create_app
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

TOKEN = "master-secret-pair"
KID = "k1"


def _jwks(priv: Ed25519PrivateKey) -> dict:
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": x, "use": "sig",
                      "alg": "EdDSA", "kid": KID}]}


def _user(priv: Ed25519PrivateKey, org: str, email: str, role: str = "owner") -> dict:
    now = int(time.time())
    token = jwt.encode({"iss": "axor-identity", "sub": f"usr_{email}", "eml": email,
                        "org": org, "role": role, "tier": "community",
                        "iat": now, "exp": now + 900}, priv, algorithm="EdDSA",
                       headers={"kid": KID})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
async def app(tmp_path: pathlib.Path, priv: Ed25519PrivateKey):  # noqa: ANN201
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN,
                     identity_jwks=_jwks(priv))
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ANN001
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c:
        yield c


def _paid(app, org: str) -> None:  # noqa: ANN001
    """Stand in a verified team-tier license for one org."""
    import axor_backend.routers.pair_api as pair_api

    paid = set(getattr(app.state, "_test_paid", set())) | {org}
    app.state._test_paid = paid
    pair_api._paid = lambda request, o: o in request.app.state._test_paid  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _restore_paid():  # noqa: ANN202
    import axor_backend.routers.pair_api as pair_api

    original = pair_api._paid
    yield
    pair_api._paid = original


async def _session(c: httpx.AsyncClient, h: dict, node: str = "claude-code-ada-1") -> None:
    r = await c.post(f"/v1/plane/{node}/profile",
                     json={"kind": "coding", "agent": "claude-code", "developer": "dev-ada"},
                     headers=h)
    assert r.status_code < 300, r.text
    r = await c.post(f"/v1/plane/{node}/telemetry", headers=h, json={
        "run_id": f"{node}-r1", "events": [
        {"schema_version": "1.0", "seq": 0, "node_id": node, "kind": "tool_call",
         "ts": "2026-11-20T10:00:00+00:00", "causal_root": None, "gate": None, "verdict": "pass",
         "payload": {"tool": "Bash", "args": {"command": "pytest -q"}}},
        {"schema_version": "1.0", "seq": 1, "node_id": node, "kind": "tool_result",
         "ts": "2026-11-20T10:00:02+00:00", "causal_root": None, "gate": None, "verdict": None,
         "payload": {"tool": "Bash", "status": "error", "preview": "1 failed",
                     "check": {"kind": "tests", "passed": False, "summary": "1 failed"}}},
        ]})
    assert r.status_code < 300, r.text


async def test_a_teammate_watches_and_the_note_reaches_the_agent(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    ada = _user(priv, "org_acme", "ada@acme.io")
    sam = _user(priv, "org_acme", "sam@acme.io", role="viewer")
    await _session(client, ada)
    r = await client.post("/v1/pair", json={"node_id": "claude-code-ada-1"}, headers=ada)
    assert r.status_code == 201, r.text
    token = r.json()["token"]
    feed = (await client.get(f"/v1/pair/{token}/feed", headers=sam)).json()
    assert feed["pair"]["you"] == "teammate" and feed["pair"]["agent"] == "claude-code"
    assert [s["kind"] for s in feed["steps"]] == ["tool_call", "tool_result"]
    assert feed["steps"][1]["check"]["passed"] is False
    # only what is new after the cursor
    again = (await client.get(f"/v1/pair/{token}/feed", headers=sam,
                              params={"after": feed["cursor"]})).json()
    assert again["steps"] == []
    r = await client.post(f"/v1/pair/{token}/notes", headers=sam,
                          json={"text": "use the fake limiter from conftest"})
    assert r.status_code == 201
    # the sidecar fetches it on the developer's key, gives it, says so
    pending = (await client.get("/v1/coding/notes/claude-code-ada-1",
                                headers=ada)).json()["notes"]
    assert [n["text"] for n in pending] == ["use the fake limiter from conftest"]
    assert pending[0]["by"] == "sam@acme.io" and pending[0]["outside"] is False
    await client.post("/v1/coding/notes/claude-code-ada-1/delivered", headers=ada,
                      json={"ids": [pending[0]["id"]], "at": "Bash"})
    left = await client.get("/v1/coding/notes/claude-code-ada-1", headers=ada)
    assert left.json()["notes"] == []
    note = (await client.get(f"/v1/pair/{token}/feed", headers=sam)).json()["notes"][0]
    assert note["delivered_at"] == "Bash" and note["delivered_ts"]


async def test_outside_the_team_needs_a_pair_opened_for_it_and_a_paid_plan(
        app, client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:  # noqa: ANN001
    ada = _user(priv, "org_acme", "ada@acme.io")
    zoe = _user(priv, "org_zoe_free", "zoe@free.dev")      # her own free workspace
    await _session(client, ada)
    # a free workspace cannot invite outside the team
    r = await client.post("/v1/pair", json={"node_id": "claude-code-ada-1", "outside": True},
                          headers=ada)
    assert r.status_code == 402 and r.json()["detail"]["code"] == "plan_required"
    # a team-only pair: zoe is not let in, and sees why
    team_only = (await client.post("/v1/pair", json={"node_id": "claude-code-ada-1"},
                                   headers=ada)).json()["token"]
    r = await client.get(f"/v1/pair/{team_only}/feed", headers=zoe)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "team_only"
    # paid: an outside pair lets zoe in, as a guest, and she can write a note
    _paid(app, "org_acme")
    token = (await client.post("/v1/pair", json={"node_id": "claude-code-ada-1", "outside": True},
                               headers=ada)).json()["token"]
    feed = (await client.get(f"/v1/pair/{token}/feed", headers=zoe)).json()
    assert feed["pair"]["you"] == "guest" and feed["pair"]["guests"] == ["zoe@free.dev"]
    assert len(feed["steps"]) == 2
    assert (await client.post(f"/v1/pair/{token}/notes", headers=zoe,
                              json={"text": "check the lockout window"})).status_code == 201
    # her note reaches the agent marked as from outside the team
    pending = (await client.get("/v1/coding/notes/claude-code-ada-1",
                                headers=ada)).json()["notes"]
    assert [(n["by"], n["outside"]) for n in pending] == [("zoe@free.dev", True)]
    # zoe still sees nothing else of acme's
    r = await client.get("/v1/runs/claude-code-ada-1-r1/events", headers=zoe)
    assert r.status_code in (403, 404) or r.json() == []
    assert "claude-code-ada-1" not in (await client.get("/v1/plane/nodes", headers=zoe)).text
    # the plan lapses: the door closes mid-session
    app.state._test_paid = set()
    r = await client.get(f"/v1/pair/{token}/feed", headers=zoe)
    assert r.status_code == 402
    # closed by the developer: gone for everyone
    _paid(app, "org_acme")
    assert (await client.delete(f"/v1/pair/{token}", headers=ada)).status_code == 200
    assert (await client.get(f"/v1/pair/{token}/feed", headers=zoe)).status_code == 404


async def test_at_most_three_outside_guests(app, client: httpx.AsyncClient,  # noqa: ANN001
                                            priv: Ed25519PrivateKey) -> None:
    ada = _user(priv, "org_acme", "ada@acme.io")
    await _session(client, ada)
    _paid(app, "org_acme")
    token = (await client.post("/v1/pair", json={"node_id": "claude-code-ada-1", "outside": True},
                               headers=ada)).json()["token"]
    for i in range(3):
        g = _user(priv, f"org_g{i}", f"g{i}@x.dev")
        assert (await client.get(f"/v1/pair/{token}/feed", headers=g)).status_code == 200
    r = await client.get(f"/v1/pair/{token}/feed", headers=_user(priv, "org_g9", "g9@x.dev"))
    assert r.status_code == 403 and r.json()["detail"]["code"] == "pair_full"
    # one already in comes back fine
    assert (await client.get(f"/v1/pair/{token}/feed",
                             headers=_user(priv, "org_g0", "g0@x.dev"))).status_code == 200


async def test_a_node_bound_key_reads_only_its_own_notes(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    master = {"Authorization": f"Bearer {TOKEN}"}
    r = await client.post("/v1/keys", headers=master,
                          json={"scopes": ["ingest"], "node_id": "claude-code-ada-1"})
    assert r.status_code < 300, r.text
    bound = {"Authorization": f"Bearer {r.json()['secret']}"}
    mine = await client.get("/v1/coding/notes/claude-code-ada-1", headers=bound)
    assert mine.status_code == 200
    assert (await client.get("/v1/coding/notes/someone-else", headers=bound)).status_code == 403
