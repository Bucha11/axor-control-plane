"""Agents of a pair write to each other: between two sessions of one developer
at once; to a teammate's agent held for their OK unless they switched to auto;
the sender's taint travels with the text; nobody outside a pair is a peer."""
from __future__ import annotations

import base64
import hashlib
import pathlib
import time
from collections.abc import AsyncIterator

import httpx
import jwt
import pytest
from axor_backend.app import create_app
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

TOKEN = "master-secret-agent-msg"
KID = "k1"


def _jwks(priv: Ed25519PrivateKey) -> dict:
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": x, "use": "sig",
                      "alg": "EdDSA", "kid": KID}]}


def _user(priv: Ed25519PrivateKey, email: str, org: str = "org_acme",
          role: str = "owner") -> dict:
    now = int(time.time())
    token = jwt.encode({"iss": "axor-identity", "sub": f"usr_{email}", "eml": email,
                        "org": org, "role": role, "tier": "community",
                        "iat": now, "exp": now + 900}, priv, algorithm="EdDSA",
                       headers={"kid": KID})
    return {"Authorization": f"Bearer {token}"}


def _dev(email: str) -> str:
    """The id the sidecar derives from the developer's git address."""
    return "dev-" + hashlib.sha256(email.encode()).hexdigest()[:16]


@pytest.fixture
def priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
async def app(tmp_path: pathlib.Path, priv: Ed25519PrivateKey):  # noqa: ANN201
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/m.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN,
                     identity_jwks=_jwks(priv))
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ANN001
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://cp.test") as c:
        yield c


MASTER = {"Authorization": f"Bearer {TOKEN}"}
# every call below acts in one workspace; a sidecar's calls are made on a
# signed-in member's credentials, as in test_pair
# (a person's own key: the plane takes the developer to be that person)
H: dict[str, dict] = {}


@pytest.fixture(autouse=True)
def _workspace(priv: Ed25519PrivateKey) -> None:
    H["sidecar"] = _user(priv, "ops@acme.io")
    H["priv"] = priv  # type: ignore[assignment]


async def _session(c: httpx.AsyncClient, node: str, email: str,
                   agent: str = "claude-code") -> None:
    """A session reported on its developer's own key."""
    r = await c.post(f"/v1/plane/{node}/profile", headers=_user(H["priv"], email),  # type: ignore[arg-type]
                     json={"kind": "coding", "agent": agent, "developer": _dev(email)})
    assert r.status_code < 300, r.text


async def _send(c: httpx.AsyncClient, frm: str, to: str, text: str,
                taint: list[str] | None = None) -> httpx.Response:
    return await c.post(f"/v1/coding/notes/{frm}/send", headers=H["sidecar"],
                        json={"to": to, "text": text, "taint": taint or []})


async def _pending(c: httpx.AsyncClient, node: str) -> dict:
    return (await c.get(f"/v1/coding/notes/{node}", headers=H["sidecar"])).json()


async def test_two_sessions_of_one_developer_talk_at_once_across_providers(
        client: httpx.AsyncClient) -> None:
    await _session(client, "cc-ada-1", "ada@acme.io")
    await _session(client, "codex-ada-2", "ada@acme.io", agent="codex")
    peers = (await client.get("/v1/coding/peers/cc-ada-1", headers=H["sidecar"])).json()["peers"]
    assert [(p["node_id"], p["relation"], p["agent"]) for p in peers] == [
        ("codex-ada-2", "own", "codex")]
    r = await _send(client, "cc-ada-1", "codex-ada-2", "I renamed User.email to User.address")
    assert r.status_code == 201 and r.json()["status"] == "queued"
    notes = (await _pending(client, "codex-ada-2"))["notes"]
    assert [(n["text"], n["from_node"], n["taint"]) for n in notes] == [
        ("I renamed User.email to User.address", "cc-ada-1", [])]
    # the reply goes back the other way
    assert (await _send(client, "codex-ada-2", "cc-ada-1", "ok, updating the serializer")
            ).status_code == 201
    thread = (await client.get("/v1/coding/notes/cc-ada-1/with/codex-ada-2",
                               headers=H["sidecar"])).json()["messages"]
    assert [m["from_node"] for m in thread] == ["cc-ada-1", "codex-ada-2"]


async def test_no_pair_no_peer(client: httpx.AsyncClient) -> None:
    await _session(client, "cc-ada-1", "ada@acme.io")
    await _session(client, "cc-sam-1", "sam@acme.io")
    assert (await client.get("/v1/coding/peers/cc-ada-1",
                             headers=H["sidecar"])).json()["peers"] == []
    r = await _send(client, "cc-ada-1", "cc-sam-1", "hi")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_a_peer"


async def test_a_teammates_agent_waits_for_their_ok_unless_they_chose_auto(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    ada, sam = _user(priv, "ada@acme.io"), _user(priv, "sam@acme.io", role="member")
    await _session(client, "cc-ada-1", "ada@acme.io")
    await _session(client, "codex-sam-1", "sam@acme.io", agent="codex")
    opened = await client.post("/v1/pair", headers=ada, json={
        "node_id": "cc-ada-1", "invite": ["sam@acme.io"]})
    assert opened.status_code == 201, opened.text
    token = opened.json()["token"]
    # both ways round: the pair joins ada's session and sam's
    a = (await client.get("/v1/coding/peers/cc-ada-1", headers=H["sidecar"])).json()["peers"]
    s = (await client.get("/v1/coding/peers/codex-sam-1", headers=H["sidecar"])).json()["peers"]
    assert [(p["node_id"], p["relation"]) for p in a] == [("codex-sam-1", "pair")]
    assert [(p["node_id"], p["relation"]) for p in s] == [("cc-ada-1", "pair")]
    assert "token" not in a[0]

    # manual (the default): sam's agent does not get it until sam says so
    r = await _send(client, "cc-ada-1", "codex-sam-1", "the API now returns 409 on dupes",
                    taint=["web"])
    assert r.json()["status"] == "held"
    held_id = r.json()["id"]
    got = await _pending(client, "codex-sam-1")
    assert got["notes"] == [] and any("wants to tell your agent" in n for n in got["news"])
    # told once
    assert not any("wants to tell" in n for n in (await _pending(client, "codex-sam-1"))["news"])
    # ada cannot send it on for sam
    r = await client.post(f"/v1/coding/say/codex-sam-1/held/{held_id}", headers=ada,
                          json={"send": True})
    assert r.status_code == 403
    r = await client.post(f"/v1/coding/say/codex-sam-1/held/{held_id}", headers=sam,
                          json={"send": True})
    assert r.status_code == 200, r.text
    notes = (await _pending(client, "codex-sam-1"))["notes"]
    assert [(n["text"], n["taint"]) for n in notes] == [
        ("the API now returns 409 on dupes", ["web"])]
    # it shows in the pair, for whoever watches it
    feed = (await client.get(f"/v1/pair/{token}/feed", headers=sam)).json()
    assert [n["from_node"] for n in feed["notes"]] == ["cc-ada-1"]

    # a dismissed one never reaches the agent
    r = await _send(client, "cc-ada-1", "codex-sam-1", "drop the table")
    await client.post(f"/v1/coding/say/codex-sam-1/held/{r.json()['id']}", headers=sam,
                      json={"send": False})
    assert all(n["text"] != "drop the table"
               for n in (await _pending(client, "codex-sam-1"))["notes"])

    # sam switches to auto: given at once
    assert (await client.get("/v1/coding/say/codex-sam-1/mode", headers=sam)
            ).json()["mode"] == "manual"
    r = await client.put("/v1/coding/say/codex-sam-1/mode", headers=sam, json={"mode": "auto"})
    assert r.status_code == 200
    assert (await client.put("/v1/coding/say/codex-sam-1/mode", headers=ada,
                             json={"mode": "auto"})).status_code == 403
    assert (await _send(client, "cc-ada-1", "codex-sam-1", "done")).json()["status"] == "queued"
    # the pair closes: no longer peers
    await client.delete(f"/v1/pair/{token}", headers=ada)
    assert (await _send(client, "cc-ada-1", "codex-sam-1", "still there?")).status_code == 403


async def test_a_bound_key_sends_only_as_its_own_session(client: httpx.AsyncClient) -> None:
    for node in ("cc-ada-1", "cc-ada-2"):
        r = await client.post(f"/v1/plane/{node}/profile", headers=MASTER, json={
            "kind": "coding", "agent": "claude-code", "developer": _dev("ada@acme.io")})
        assert r.status_code < 300, r.text
    r = await client.post("/v1/keys", headers=MASTER,
                          json={"scopes": ["ingest"], "node_id": "cc-ada-1"})
    bound = {"Authorization": f"Bearer {r.json()['secret']}"}
    ok = await client.post("/v1/coding/notes/cc-ada-1/send", headers=bound,
                           json={"to": "cc-ada-2", "text": "hi"})
    assert ok.status_code == 201, ok.text
    no = await client.post("/v1/coding/notes/cc-ada-2/send", headers=bound,
                           json={"to": "cc-ada-1", "text": "hi"})
    assert no.status_code == 403


def test_the_feed_shows_requests_what_the_agent_says_and_its_edits() -> None:
    from axor_backend.routers.pair_api import _step

    said = _step(1, {"kind": "intent", "ts": "t",
                     "payload": {"role": "developer", "text": "rename the field"}})
    assert said == {"id": 1, "ts": "t", "kind": "said", "role": "developer",
                    "text": "rename the field"}
    edit = _step(2, {"kind": "tool_call", "payload": {"tool": "Edit", "args": {
        "file_path": "models.py", "old_string": "email", "new_string": "address"}}})
    assert edit["diff"] == {"path": "models.py", "hunks": [{"old": "email", "new": "address"}]}
    multi = _step(3, {"kind": "tool_call", "payload": {"tool": "MultiEdit", "args": {
        "file_path": "a.py", "edits": [{"old_string": "x", "new_string": "y"}]}}})
    assert multi["diff"]["hunks"] == [{"old": "x", "new": "y"}]
    write = _step(4, {"kind": "tool_call", "payload": {"tool": "Write", "args": {
        "file_path": "b.py", "content": "print(1)"}}})
    assert write["diff"]["hunks"] == [{"old": "", "new": "print(1)"}]
    patch = _step(5, {"kind": "tool_call", "payload": {"tool": "apply_patch", "args": {
        "input": "*** Begin Patch\n*** Update File: c.py\n-a\n+b\n*** End Patch"}}})
    assert patch["diff"]["patch"].startswith("*** Begin Patch")
    assert "diff" not in _step(6, {"kind": "tool_call", "payload": {
        "tool": "Bash", "args": {"command": "ls"}}})
    # an intent with no role (a production agent's) stays what it was
    assert _step(7, {"kind": "intent", "payload": {"text": "x"}})["kind"] == "task"


async def test_the_pair_sees_a_hold_by_whom_and_when_the_agent_confirms_it(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    ada = _user(priv, "ada@acme.io")
    await _session(client, "cc-ada-1", "ada@acme.io")
    opened = await client.post("/v1/pair", headers=ada, json={"node_id": "cc-ada-1"})
    token = opened.json()["token"]
    r = await client.post("/v1/plane/cc-ada-1/command", headers=ada,
                          json={"version": 1, "state": {"paused": True}})
    assert r.status_code < 300, r.text
    feed = (await client.get(f"/v1/pair/{token}/feed", headers=ada)).json()
    assert feed["pair"]["paused"] is True and feed["pair"]["confirmed"] is False
    assert [(c["act"], c["by"]) for c in feed["controls"]] == [("held", "ada@acme.io")]
    # the sidecar reports it applied the command
    await client.post("/v1/plane/cc-ada-1/telemetry", headers=ada, json={
        "run_id": "cc-ada-1", "events": [{
            "schema_version": "1.0", "seq": 1, "node_id": "cc-ada-1", "kind": "heartbeat",
            "ts": "2026-11-25T00:00:00+00:00", "causal_root": None, "gate": None, "verdict": None,
            "payload": {"applied_version": 1, "level": "NORMAL"}}]})
    feed = (await client.get(f"/v1/pair/{token}/feed", headers=ada)).json()
    assert feed["pair"]["confirmed"] is True
