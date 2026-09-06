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
    # Influence ranking is subgraph ablation — replay, no state change — so it
    # belongs with its siblings, not with the ingest prefix it sat under.
    assert required_scope("POST", "/v1/runs/r/influence") == "read"
    assert required_scope("POST", "/v1/replay/r") == "read"
    assert required_scope("POST", "/v1/regression") == "read"
    # A node reports its own health out-dial, exactly like telemetry.
    assert required_scope("POST", "/v1/plane/n1/probe-report") == "ingest"

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

    # The health channel is bound the same way — a clean verdict for a
    # neighbour is exactly the report worth forging. Assert the REASON: this
    # route used to need `operate`, so an ingest key was refused before the
    # binding was ever consulted and this test passed without exercising it.
    health = await secured.post(
        "/v1/plane/node-b/probe-report",
        json={"overall_verdict": "CONSISTENT", "families": []},
        headers=_bearer(key),
    )
    assert health.status_code == 403
    assert "may not post as" in health.json()["detail"]

    # …and the node's own health check goes through, which is the half that
    # was broken: axor-wrap raises on a 4xx here, so a battery crashed.
    own_health = await secured.post(
        "/v1/plane/node-a/probe-report",
        json={"overall_verdict": "CONSISTENT", "families": []},
        headers=_bearer(key),
    )
    assert own_health.status_code == 201


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


# ── the path the policy is matched on ─────────────────────────────────────────

class TestTheScopeSurvivesTheDeploymentShape:
    """Every policy table in `auth` matches a prefix of the request path, so the
    policy is exactly as good as the agreement between the string it matches and
    the string the router matches.

    Mounted behind a path prefix — `uvicorn --root-path /api`, an ordinary
    reverse proxy — ASGI hands the app the FULL path and names the mount in
    `root_path`. The router strips it; the middleware read `request.url.path`
    and did not. So `/api/v1/keys` matched no entry, fell through the
    GET-defaults-to-`read` rule, and a READ-ONLY key listed every credential in
    the deployment. Writes fell to the unknown-path default of `operate`, which
    mints API keys and enrolls vault credentials. Nothing was logged: the
    request was authorized, at the wrong bar.
    """

    @staticmethod
    async def _asgi(app, method: str, path: str, *, token: str | None = None,  # noqa: ANN001
                    body: bytes = b"", root_path: str = "") -> int:
        """A raw ASGI call: an HTTP client normalizes paths and cannot set
        root_path, so neither can reach what this is about."""
        headers = [(b"host", b"t"), (b"content-type", b"application/json")]
        if token:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": method, "path": path, "raw_path": path.encode(),
            "root_path": root_path, "scheme": "http", "query_string": b"",
            "headers": headers, "client": ("1.2.3.4", 1), "server": ("t", 80),
        }
        status: dict[str, int] = {}
        sent = {"done": False}

        async def receive() -> dict:
            if sent["done"]:
                return {"type": "http.disconnect"}
            sent["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message: dict) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]

        await app(scope, receive, send)
        return status["code"]

    @pytest.fixture
    async def mounted(self, tmp_path: pathlib.Path):  # noqa: ANN201
        """The app plus a read-only and an operate key — the two credentials the
        collapsed bars handed the wrong surfaces to."""
        app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/m.db",
                         operator_keys={}, allow_unsigned=True, api_token=TOKEN)
        async with app.router.lifespan_context(app):
            async with _client(app) as c:
                read = await c.post("/v1/keys", json={"scopes": ["read"]},
                                    headers=_bearer(TOKEN))
                operate = await c.post("/v1/keys", json={"scopes": ["operate"]},
                                       headers=_bearer(TOKEN))
            yield app, read.json()["secret"], operate.json()["secret"]

    async def test_a_read_key_cannot_list_credentials_behind_a_mount(
        self, mounted: tuple,
    ) -> None:
        app, read_key, _ = mounted
        assert await self._asgi(app, "GET", "/api/v1/keys",
                                token=read_key, root_path="/api") == 403

    async def test_an_operate_key_cannot_mint_or_enroll_behind_a_mount(
        self, mounted: tuple,
    ) -> None:
        """Writes to an unmatched path fall to the unknown-path default of
        `operate`, so behind a mount an OPERATE key — an ordinary incident-
        response credential — could mint itself an admin API key and enroll a
        tool credential in the vault. A read key would have been refused either
        way; this is the credential the collapse actually promoted."""
        app, _, operate_key = mounted
        assert await self._asgi(app, "POST", "/api/v1/keys", token=operate_key,
                                body=b'{"scopes":["admin"]}', root_path="/api") == 403
        assert await self._asgi(app, "POST", "/api/v1/vault/creds/enroll",
                                token=operate_key, body=b"{}", root_path="/api") == 403

    async def test_the_master_token_still_works_behind_a_mount(
        self, mounted: tuple,
    ) -> None:
        """Fixing the bar must not raise it for the credential that has every
        scope — otherwise a mounted deployment is simply broken."""
        app, _, _operate = mounted
        assert await self._asgi(app, "GET", "/api/v1/keys",
                                token=TOKEN, root_path="/api") == 200

    async def test_an_open_route_is_still_open_behind_a_mount(
        self, mounted: tuple,
    ) -> None:
        """`is_open` matched the un-stripped path too, so health checks and the
        share surface stopped being open the moment the app was mounted."""
        app, _read, _operate = mounted
        assert await self._asgi(app, "GET", "/api/v1/healthz", root_path="/api") == 200

    def test_redundant_separators_do_not_lower_the_bar(self) -> None:
        """`//v1/keys` starts with no policy entry at all. Starlette happens not
        to route it, so it 404s rather than escalating — but that is the router
        saving the policy, not the policy being right. It is judged as the route
        it spells."""
        from axor_backend.auth import policy_path, required_scope

        for path in ("//v1/keys", "/v1//keys", "/v1/./keys"):
            assert required_scope("GET", policy_path(path)) == "admin", path


def test_a_dot_dot_path_is_judged_as_the_route_it_spells() -> None:
    """`/v1/share/` is served without authentication, and `is_open` matched by
    prefix on the raw path, so `/v1/share/../keys` read as open. The router
    404s it, but a policy that calls the credential list open is one proxy
    normalization away from meaning it."""
    from axor_backend.auth import is_open, policy_path, required_scope

    canonical = policy_path("/v1/share/../keys")
    assert canonical == "/v1/keys"
    assert not is_open("GET", canonical)
    assert required_scope("GET", canonical) == "admin"


def test_a_plane_path_naming_no_node_claims_no_node() -> None:
    """`plane_node_of` took everything before the last separator, so
    `/v1/plane/telemetry` — which names no node — yielded the node id
    "telemetry", and a nested path yielded "a/b", which is not a node id any key
    can be bound to. Only the single `{node_id}` segment the router binds."""
    from axor_backend.auth import plane_node_of

    assert plane_node_of("POST", "/v1/plane/n1/telemetry") == "n1"
    assert plane_node_of("POST", "/v1/plane/telemetry") is None
    assert plane_node_of("POST", "/v1/plane/a/b/telemetry") is None
