"""Product auth: local token + scoped API keys (architecture section 9).

Auth is opt-in (off unless a master token is set), the master token is
all-scope, minted keys are least-privilege, and the gate composes with — never
replaces — the plane's command signing.
"""
from __future__ import annotations

import pathlib

import httpx
import pytest
from axor_backend.app import create_app

TOKEN = "master-secret-xyz"


def _client(app) -> httpx.AsyncClient:  # noqa: ANN001
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
    )


@pytest.fixture
async def open_client(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/o.db",
                     operator_keys={}, allow_unsigned=True, api_token=None)
    async with _client(app) as c, app.router.lifespan_context(app):
        yield c


@pytest.fixture
async def secured(tmp_path: pathlib.Path) -> httpx.AsyncClient:
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN)
    async with _client(app) as c, app.router.lifespan_context(app):
        yield c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── opt-in posture ────────────────────────────────────────────────────────────

async def test_auth_off_by_default_everything_open(open_client: httpx.AsyncClient) -> None:
    assert (await open_client.get("/v1/runs")).status_code == 200
    status = (await open_client.get("/v1/auth/status")).json()
    assert status["auth_enabled"] is False and status["authenticated"] is True


async def test_enabled_blocks_unauthenticated(secured: httpx.AsyncClient) -> None:
    assert (await secured.get("/v1/runs")).status_code == 401
    # healthz + auth status stay open
    assert (await secured.get("/v1/healthz")).status_code == 200
    status = (await secured.get("/v1/auth/status")).json()
    assert status["auth_enabled"] is True and status["authenticated"] is False


# ── master token ──────────────────────────────────────────────────────────────

async def test_master_token_has_all_scopes(secured: httpx.AsyncClient) -> None:
    assert (await secured.get("/v1/runs", headers=_bearer(TOKEN))).status_code == 200
    status = (await secured.get("/v1/auth/status", headers=_bearer(TOKEN))).json()
    assert status["authenticated"] is True
    assert set(status["scopes"]) == {"read", "ingest", "operate", "admin"}


async def test_bad_token_rejected(secured: httpx.AsyncClient) -> None:
    assert (await secured.get("/v1/runs", headers=_bearer("nope"))).status_code == 401


# ── scoped API keys ───────────────────────────────────────────────────────────

async def test_minted_key_is_least_privilege(secured: httpx.AsyncClient) -> None:
    # minting needs admin (the master token)
    assert (await secured.post("/v1/keys", json={"scopes": ["ingest"]})).status_code == 401
    made = await secured.post(
        "/v1/keys", json={"scopes": ["ingest"], "label": "proxy"},
        headers=_bearer(TOKEN),
    )
    assert made.status_code == 201
    key = made.json()["secret"]
    assert made.json()["key_id"] in key  # key_id prefixes the secret

    # the ingest key may ingest…
    ingest = await secured.post(
        "/v1/ingest/run_k", json={"node_id": "n", "events": []},
        headers=_bearer(key),
    )
    assert ingest.status_code == 202
    # …but not operate (plane command) nor admin (mint keys)
    cmd = await secured.post(
        "/v1/plane/n/command",
        json={"version": 1, "state": {"paused": True}, "operator": "op",
              "timestamp": "t", "sig": ""},
        headers=_bearer(key),
    )
    assert cmd.status_code == 403
    assert cmd.json()["need"] == "operate"
    assert (await secured.post("/v1/keys", json={"scopes": ["read"]},
                               headers=_bearer(key))).status_code == 403


async def test_read_key_cannot_ingest(secured: httpx.AsyncClient) -> None:
    key = (await secured.post("/v1/keys", json={"scopes": ["read"]},
                              headers=_bearer(TOKEN))).json()["secret"]
    assert (await secured.get("/v1/runs", headers=_bearer(key))).status_code == 200
    ingest = await secured.post("/v1/ingest/r", json={"events": []},
                                headers=_bearer(key))
    assert ingest.status_code == 403


async def test_key_revocation(secured: httpx.AsyncClient) -> None:
    made = (await secured.post("/v1/keys", json={"scopes": ["read"]},
                               headers=_bearer(TOKEN))).json()
    key, key_id = made["secret"], made["key_id"]
    assert (await secured.get("/v1/runs", headers=_bearer(key))).status_code == 200
    assert (await secured.delete(f"/v1/keys/{key_id}",
                                 headers=_bearer(TOKEN))).status_code == 200
    assert (await secured.get("/v1/runs", headers=_bearer(key))).status_code == 401


async def test_sse_accepts_token_query_param(secured: httpx.AsyncClient) -> None:
    # EventSource can't set headers → the stream endpoint honours ?token=.
    # (We only assert the gate lets it through, not the stream body.)
    await secured.post("/v1/ingest/run_s", json={"node_id": "n", "events": [
        {"schema_version": "1.0", "seq": 0, "node_id": "n", "kind": "claim",
         "ts": "t", "causal_root": None, "gate": None, "verdict": None,
         "payload": {}},
    ]}, headers=_bearer(TOKEN))
    no_tok = await secured.get("/v1/runs/run_s/events")
    assert no_tok.status_code == 401
    with_tok = await secured.get(f"/v1/runs/run_s/events?token={TOKEN}")
    assert with_tok.status_code == 200


async def test_share_link_stays_open_under_auth(secured: httpx.AsyncClient) -> None:
    # a share permalink carries its own scoped token by design (§8.3) — it must
    # resolve without the dashboard token.
    assert (await secured.get("/v1/share/nonexistent")).status_code == 404  # not 401
