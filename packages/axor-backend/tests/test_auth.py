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


# ── privileged reads: scopes must gate the surfaces whose CONTENTS are secret ──

async def test_listing_keys_needs_admin_not_read(secured: httpx.AsyncClient) -> None:
    """Regression (F-07): `required_scope` returned "read" for every GET before
    it consulted the policy table, so the `/v1/keys` -> admin rule only ever
    guarded minting. A read-only key could enumerate every credential in the
    org — ids, scopes, labels, dates."""
    read_key = (await secured.post("/v1/keys", json={"scopes": ["read"]},
                                   headers=_bearer(TOKEN))).json()["secret"]
    listed = await secured.get("/v1/keys", headers=_bearer(read_key))
    assert listed.status_code == 403
    assert listed.json()["need"] == "admin"
    assert (await secured.get("/v1/keys", headers=_bearer(TOKEN))).status_code == 200


async def test_vault_inventory_is_not_a_read_surface(
    secured: httpx.AsyncClient,
) -> None:
    """Regression (F-12): with no per-subsystem vault token configured — the
    docker-compose default — the vault's own gate opens, leaving the scope
    ladder as the only check. The creds health lists every enrolment with its
    dispense scope; the signing audit is the record of who signed what."""
    read_key = (await secured.post("/v1/keys", json={"scopes": ["read"]},
                                   headers=_bearer(TOKEN))).json()["secret"]
    for path, need in (("/v1/vault/creds/health", "admin"),
                       ("/v1/vault/signing/audit", "admin"),
                       ("/v1/vault/signing/keys", "operate")):
        denied = await secured.get(path, headers=_bearer(read_key))
        assert denied.status_code == 403, path
        assert denied.json()["need"] == need, path
        assert (await secured.get(path, headers=_bearer(TOKEN))).status_code == 200


async def test_vault_operational_routes_are_not_over_gated() -> None:
    """The two vault subsystems are not one surface. Gating the whole prefix at
    admin would have broken the two routes that are not operator config: a
    governed node fetching its own tool credential at call time, and the UI
    signing an operator command. Both keep their own separate checks (the
    per-node dispense scope; the signing token plus the operator allowlist)."""
    from axor_backend.auth import required_scope

    assert required_scope("POST", "/v1/vault/creds/dispense") == "ingest"
    assert required_scope("POST", "/v1/vault/signing/sign") == "operate"
    assert required_scope("POST", "/v1/vault/creds/revoke") == "operate"
    # …while custody CONFIG stays admin.
    assert required_scope("POST", "/v1/vault/creds/enroll") == "admin"
    assert required_scope("POST", "/v1/vault/creds/rotate") == "admin"
    assert required_scope("POST", "/v1/vault/signing/keys") == "admin"


# ── open paths open ONE verb, not a whole prefix ──────────────────────────────

async def test_share_revocation_is_not_open(secured: httpx.AsyncClient) -> None:
    """Regression (F-08): `/v1/share/` was opened as a bare prefix, so DELETE
    on it skipped the gate entirely — anyone holding a forwarded link could
    burn it. Reading a share link stays open by design (§8.3); revoking it is
    an operator action."""
    assert (await secured.get("/v1/share/nonexistent")).status_code == 404  # open
    assert (await secured.delete("/v1/share/nonexistent")).status_code == 401
    # with a credential it reaches the handler (404: no such token)
    assert (await secured.delete("/v1/share/nonexistent",
                                 headers=_bearer(TOKEN))).status_code == 404


async def test_license_activation_needs_admin(secured: httpx.AsyncClient) -> None:
    """Regression (F-09): `/v1/license/verify` was open as "a pure utility over
    user-supplied input", but it STORES and ACTIVATES the license. Anyone
    holding any vendor-signed licence could downgrade a paid deployment."""
    anon = await secured.post("/v1/license/verify", json={"license_json": "{}"})
    assert anon.status_code == 401
    read_key = (await secured.post("/v1/keys", json={"scopes": ["read"]},
                                   headers=_bearer(TOKEN))).json()["secret"]
    scoped = await secured.post("/v1/license/verify", json={"license_json": "{}"},
                                headers=_bearer(read_key))
    assert scoped.status_code == 403
    assert scoped.json()["need"] == "admin"


async def test_minting_a_share_link_needs_operate(secured: httpx.AsyncClient) -> None:
    """Regression (F-11): `/v1/runs/` is ingest so the proxy can POST evidence,
    which swallowed `.../cases/{i}/share` — the upload credential doubled as a
    publishing one. Minting a permalink is an operator decision."""
    from axor_backend.auth import required_scope

    assert required_scope("POST", "/v1/runs/r/evidence") == "ingest"
    assert required_scope("POST", "/v1/runs/r/cases/0/share") == "operate"

    ingest_key = (await secured.post("/v1/keys", json={"scopes": ["ingest"]},
                                     headers=_bearer(TOKEN))).json()["secret"]
    denied = await secured.post("/v1/runs/r/cases/0/share",
                                headers=_bearer(ingest_key))
    assert denied.status_code == 403
    assert denied.json()["need"] == "operate"


# ── a key may be bound to one node ────────────────────────────────────────────

def _heartbeat(node_id: str) -> dict:
    return {"events": [{"schema_version": "1.0", "seq": 0, "node_id": node_id,
                        "kind": "heartbeat", "ts": "t", "causal_root": None,
                        "gate": None, "verdict": None,
                        "payload": {"applied_version": 0, "level": "NORMAL",
                                    "budget_remaining": None}}]}


async def test_node_bound_key_cannot_speak_for_another_node(
    secured: httpx.AsyncClient,
) -> None:
    """Regression (F-10): scopes said what a credential may DO, never who it may
    do it AS, so any ingest key could forge a neighbour's heartbeat (its level
    and budget), silence that neighbour's node_stale page, or replace a
    DRIFT_DETECTED health verdict with a clean one."""
    made = await secured.post(
        "/v1/keys", json={"scopes": ["ingest"], "node_id": "node-a"},
        headers=_bearer(TOKEN),
    )
    assert made.status_code == 201 and made.json()["node_id"] == "node-a"
    key = made.json()["secret"]

    own = await secured.post("/v1/plane/node-a/telemetry",
                             json=_heartbeat("node-a"), headers=_bearer(key))
    assert own.status_code == 202

    forged = await secured.post("/v1/plane/node-b/telemetry",
                                json=_heartbeat("node-b"), headers=_bearer(key))
    assert forged.status_code == 403
    assert "may not post as" in forged.json()["detail"]

    # the health channel is bound the same way — a clean verdict for a
    # neighbour is exactly the report worth forging
    health = await secured.post(
        "/v1/plane/node-b/probe-report",
        json={"overall_verdict": "CONSISTENT", "families": []},
        headers=_bearer(key),
    )
    assert health.status_code == 403


async def test_unbound_key_still_speaks_for_the_fleet(
    secured: httpx.AsyncClient,
) -> None:
    """The binding is additive: a key minted without a node_id — every key that
    predates the column, and every fleet-wide operator key — is unchanged."""
    key = (await secured.post("/v1/keys", json={"scopes": ["ingest"]},
                              headers=_bearer(TOKEN))).json()["secret"]
    for node in ("node-a", "node-b"):
        posted = await secured.post(f"/v1/plane/{node}/telemetry",
                                    json=_heartbeat(node), headers=_bearer(key))
        assert posted.status_code == 202


async def test_node_binding_does_not_gate_operator_commands() -> None:
    """A command is aimed AT a node, not spoken BY it, so it is not a
    speaks-as route (it carries its own ed25519 check instead)."""
    from axor_backend.auth import plane_node_of

    assert plane_node_of("POST", "/v1/plane/n1/telemetry") == "n1"
    assert plane_node_of("POST", "/v1/plane/n1/probe-report") == "n1"
    assert plane_node_of("POST", "/v1/plane/n1/facts") == "n1"
    assert plane_node_of("POST", "/v1/plane/n1/command") is None
    assert plane_node_of("POST", "/v1/plane/n1/cascade-stop") is None
    assert plane_node_of("GET", "/v1/plane/n1/probe-report") is None
    assert plane_node_of("POST", "/v1/ingest/r1") is None
