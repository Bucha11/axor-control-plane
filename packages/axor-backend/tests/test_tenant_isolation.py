"""Per-org data isolation: one org's runs are invisible to another org and to
the operator (public) principal, and vice versa.

The store is scoped by the request principal's org (tenancy.py). A run ingested
under org A must not appear in org B's listing, org B's event stream, or the
operator master token's (public) listing.
"""
from __future__ import annotations

import base64
import pathlib
import time

import httpx
import pytest
from axor_backend.app import create_app

jwt = pytest.importorskip("jwt")
pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

TOKEN = "master-secret-xyz"
KID = "k1"


def _jwks(priv: Ed25519PrivateKey) -> dict:
    raw = priv.public_key().public_bytes(serialization.Encoding.Raw,
                                         serialization.PublicFormat.Raw)
    x = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    return {"keys": [{"kty": "OKP", "crv": "Ed25519", "x": x, "use": "sig",
                      "alg": "EdDSA", "kid": KID}]}


@pytest.fixture
def priv() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture
async def client(tmp_path: pathlib.Path, priv: Ed25519PrivateKey):  # noqa: ANN201
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/t.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN,
                     identity_jwks=_jwks(priv))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


def _tok(priv: Ed25519PrivateKey, org: str, role: str = "owner") -> str:
    now = int(time.time())
    return jwt.encode(
        {"iss": "axor-identity", "sub": f"u_{org}", "eml": f"a@{org}.io", "org": org,
         "role": role, "tier": "team", "iat": now, "exp": now + 900},
        priv, algorithm="EdDSA", headers={"kid": KID})


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _ingest(client: httpx.AsyncClient, token: str, run_id: str) -> None:
    r = await client.post(f"/v1/ingest/{run_id}", headers=_bearer(token),
                         json={"node_id": "n1", "scenario": "custom",
                               "events": [{"seq": 0, "kind": "decision",
                                           "node_id": "n1"}]})
    assert r.status_code == 202, r.text


async def _run_ids(client: httpx.AsyncClient, token: str) -> set[str]:
    r = await client.get("/v1/runs", headers=_bearer(token))
    assert r.status_code == 200, r.text
    runs = r.json()
    rows = runs if isinstance(runs, list) else runs.get("runs", runs)
    return {row["run_id"] for row in rows}


async def test_a_run_is_visible_only_within_its_org(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    org_a, org_b = _tok(priv, "org_a"), _tok(priv, "org_b")
    await _ingest(client, org_a, "run_a")

    assert "run_a" in await _run_ids(client, org_a)      # its own org sees it
    assert "run_a" not in await _run_ids(client, org_b)  # another org does not
    assert "run_a" not in await _run_ids(client, TOKEN)  # operator (public) does not


async def test_events_do_not_cross_orgs(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    org_a, org_b = _tok(priv, "org_a"), _tok(priv, "org_b")
    await _ingest(client, org_a, "run_x")
    # org B cannot read org A's run events
    a_events = await client.get("/v1/runs/run_x/events", headers=_bearer(org_a))
    b_events = await client.get("/v1/runs/run_x/events", headers=_bearer(org_b))
    assert a_events.json() and not b_events.json()


async def test_public_and_org_data_are_separate(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    # ingest under the operator (public) principal and under an org; neither sees
    # the other's run
    await _ingest(client, TOKEN, "run_pub")
    await _ingest(client, _tok(priv, "org_a"), "run_a")
    assert await _run_ids(client, TOKEN) == {"run_pub"}
    assert await _run_ids(client, _tok(priv, "org_a")) == {"run_a"}


# ── 0009: the KEY SPACE is per tenant, not only the read filter ───────────────

async def test_two_orgs_may_hold_the_same_run_id(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    """Regression (F-01): 0007 scoped every READ by org but left the primary
    keys single-column, so the existence check ran WITH the org filter and the
    insert without. The second tenant to use an id got an IntegrityError — a
    500. For a Lab handoff this is not hypothetical: pins are the deterministic
    `lab:{trace_id}`, identical for everyone who imports the same package."""
    org_a, org_b = _tok(priv, "org_a"), _tok(priv, "org_b")
    for token in (org_a, org_b, TOKEN):
        await _ingest(client, token, "lab:trace1")
    assert "lab:trace1" in await _run_ids(client, org_a)
    assert "lab:trace1" in await _run_ids(client, org_b)
    assert "lab:trace1" in await _run_ids(client, TOKEN)


async def test_two_orgs_may_run_a_node_of_the_same_name(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    """Same defect on the plane: desired_state and reported_state were keyed by
    node_id alone, so naming a node `orchestrator` in one org locked every other
    org out of that name."""
    org_a, org_b = _tok(priv, "org_a"), _tok(priv, "org_b")
    for token, paused in ((org_a, True), (org_b, False)):
        r = await client.post(
            "/v1/plane/orchestrator/command", headers=_bearer(token),
            json={"version": 1, "state": {"paused": paused}, "operator": "op",
                  "timestamp": "t", "sig": ""},
        )
        assert r.status_code == 202, r.text
    a = await client.get("/v1/plane/nodes", headers=_bearer(org_a))
    b = await client.get("/v1/plane/nodes", headers=_bearer(org_b))
    assert a.json()[0]["desired"]["state"] == {"paused": True}
    assert b.json()[0]["desired"]["state"] == {"paused": False}


async def test_taint_graph_does_not_cross_orgs(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    """Regression (F-02): one InMemoryGraphStore served the whole process, and
    `/v1/graph/khop` never filtered — so a guessed value ref returned another
    tenant's provenance edges, complete with the run id they came from."""
    org_a, org_b = _tok(priv, "org_a"), _tok(priv, "org_b")
    await client.post("/v1/ingest/secret_run", headers=_bearer(org_a), json={
        "node_id": "n", "events": [
            {"schema_version": "1.0", "seq": 0, "node_id": "n",
             "kind": "tool_call", "ts": "t", "causal_root": None, "gate": None,
             "verdict": None,
             "payload": {"tool": "read_payroll", "arg_refs": {"q": "v_query"}}},
            {"schema_version": "1.0", "seq": 1, "node_id": "n",
             "kind": "tool_result", "ts": "t", "causal_root": None,
             "gate": None, "verdict": None,
             "payload": {"tool": "read_payroll", "value_ref": "v_salaries",
                         "root": {"sources": ["web"], "sensitive": True}}},
        ]})
    mine = await client.get("/v1/graph/khop?focus=v_query&k=2", headers=_bearer(org_a))
    theirs = await client.get("/v1/graph/khop?focus=v_query&k=2", headers=_bearer(org_b))
    assert mine.json()["edges"], "the owning org still sees its own derivation"
    assert theirs.json()["edges"] == []
    assert "v_salaries" not in theirs.json()["nodes"]


async def test_settings_kv_does_not_cross_orgs(tmp_path: pathlib.Path) -> None:
    """Regression (F-03): the settings KV was global, and it holds BOTH
    federation vaults — enrolled tool credentials, signing-key seeds and the
    signing audit — plus the licence and the regression schedule. One tenant's
    get_setting returned another tenant's vault."""
    from axor_backend.storage import Store, init_db, make_engine
    from axor_backend.tenancy import PUBLIC_ORG, set_current_org

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/kv.db")
    await init_db(engine)
    store = Store(engine)
    try:
        set_current_org("org_a")
        await store.set_setting("vault_creds/v1", {"secret": "org_a_secret"})
        set_current_org("org_b")
        assert await store.get_setting("vault_creds/v1") is None
        await store.set_setting("vault_creds/v1", {"secret": "org_b_secret"})
        set_current_org("org_a")
        assert await store.get_setting("vault_creds/v1") == {"secret": "org_a_secret"}
    finally:
        set_current_org(PUBLIC_ORG)
        await engine.dispose()


async def test_background_sweeps_see_every_tenant(tmp_path: pathlib.Path) -> None:
    """Regression (F-04): the retention, scheduler and stale-monitor loops are
    background tasks, so they carry no request and the ambient tenant is the
    public one. They swept it alone — meaning an identity org's node could go
    silent forever without a page, and its scheduled corpus run never fired.
    `list_orgs` is what lets each loop iterate tenants instead."""
    from axor_backend.storage import Store, init_db, make_engine
    from axor_backend.tenancy import PUBLIC_ORG, set_current_org

    engine = make_engine(f"sqlite+aiosqlite:///{tmp_path}/sweep.db")
    await init_db(engine)
    store = Store(engine)
    try:
        set_current_org("org_a")
        await store.upsert_reported("n1", 1, "NORMAL", None, "2026-01-01T00:00:00Z")
        set_current_org("org_b")
        await store.set_setting("regression_schedule", {"enabled": True})
        set_current_org(PUBLIC_ORG)

        assert set(await store.list_orgs()) >= {PUBLIC_ORG, "org_a", "org_b"}
        # the scheduler asks specifically for tenants that configured one
        assert await store.orgs_with_setting("regression_schedule") == ["org_b"]
    finally:
        set_current_org(PUBLIC_ORG)
        await engine.dispose()


def test_broadcast_topics_are_namespaced_by_tenant() -> None:
    """Regression (F-05): the SSE bus is addressed by strings, and they were
    global (`plane:{node_id}`). Historical reads were org-filtered in SQL; the
    LIVE stream was not, so a `read`-scoped subscriber naming another tenant's
    node received its desired state and telemetry."""
    from axor_backend.broadcast import Broadcast
    from axor_backend.tenancy import topic

    bus = Broadcast()
    a = bus.subscribe(topic("plane", "orchestrator", "org_a"))
    b = bus.subscribe(topic("plane", "orchestrator", "org_b"))
    bus.publish(topic("plane", "orchestrator", "org_a"), {"secret": "for a"})

    assert a.qsize() == 1
    assert b.qsize() == 0, "the same node name in another org is a different topic"


async def test_share_link_resolves_for_an_identity_org(
        client: httpx.AsyncClient, priv: Ed25519PrivateKey) -> None:
    """Regression (F-06): `/v1/share/{token}` is an OPEN route, so the
    middleware never stamped a tenant and the case lookup ran under the public
    one — 404 for every link an identity user had created. The link now carries
    its org and the route adopts it."""
    org_a, org_b = _tok(priv, "org_a"), _tok(priv, "org_b")
    await _ingest(client, org_a, "run_share")
    await client.post("/v1/runs/run_share/evidence", headers=_bearer(org_a),
                      json={"evidence": [{"deviation": "d",
                                          "observed_reality": "o",
                                          "agent_claim": "c"}]})
    made = await client.post("/v1/runs/run_share/cases/0/share",
                             headers=_bearer(org_a))
    assert made.status_code == 200, made.text
    token = made.json()["token"]

    # resolves with no credential at all, under the LINK's tenant
    opened = await client.get(f"/v1/share/{token}")
    assert opened.status_code == 200
    assert "EvidenceCase" in opened.text

    # …but another tenant cannot burn it
    assert (await client.delete(f"/v1/share/{token}",
                                headers=_bearer(org_b))).status_code == 404
    assert (await client.get(f"/v1/share/{token}")).status_code == 200
    assert (await client.delete(f"/v1/share/{token}",
                                headers=_bearer(org_a))).status_code == 200
    assert (await client.get(f"/v1/share/{token}")).status_code == 404


# ── entitlement is per tenant, and the operator is not a tenant ───────────────

class TestLicensingAcrossTenants:
    """The control plane is not only self-hosted: one process, many tenants, is
    what `tenancy.py` and the identity login exist for. Entitlement has to
    follow the tenant, and installing it has to be possible for whoever runs the
    deployment.

    It was not. The operator's master token is fleet-wide and therefore resolves
    to the PUBLIC organization, so an operator installing a customer's license
    got a 200 naming that customer and quietly wrote it to `public` — licensing
    the wrong tenant with someone else's file, and leaving the customer with
    nothing. Now an operator names the tenant; a login cannot.
    """

    @staticmethod
    def _vendor() -> tuple[str, str]:
        from nacl.signing import SigningKey

        key = SigningKey.generate()
        return bytes(key).hex(), bytes(key.verify_key).hex()

    @staticmethod
    def _license(priv: str, org: str) -> str:
        from axor_backend.ee.license import sign_license

        return sign_license(
            {"organization": org, "workspace_tier": "team",
             "modules": {"private_lab": True, "control_plane": True},
             "governed_node_ceiling": 10, "self_hosted_runner": False,
             "expires_at": "2099-01-01", "features": []},
            priv,
        )

    @pytest.fixture
    async def hosted(self, tmp_path: pathlib.Path, priv: Ed25519PrivateKey):  # noqa: ANN201
        vpriv, vpub = self._vendor()
        app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/h.db",
                         operator_keys={}, allow_unsigned=True, api_token=TOKEN,
                         identity_jwks=_jwks(priv), vendor_pubkey=vpub)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://cp.test",
        ) as c, app.router.lifespan_context(app):
            yield c, vpriv, app

    async def test_the_operator_licenses_the_tenant_it_names(
        self, hosted: tuple, priv: Ed25519PrivateKey,
    ) -> None:
        client, vpriv, app = hosted
        r = await client.post("/v1/license/verify",
                              headers={"Authorization": f"Bearer {TOKEN}"},
                              json={"license_json": self._license(vpriv, "acme"),
                                    "org": "acme"})
        assert r.status_code == 200, r.text
        assert r.json()["org"] == "acme"
        # the tenant has it …
        acme = {"Authorization": f"Bearer {_tok(priv, 'acme')}"}
        status = (await client.get("/v1/license/status", headers=acme)).json()
        assert status["active"] is True
        # … and the public tenant, which the operator's own token resolves to,
        # did NOT quietly get someone else's license
        operator = (await client.get("/v1/license/status",
                                     headers={"Authorization": f"Bearer {TOKEN}"})).json()
        assert operator["active"] is False
        assert "public" not in app.state.licenses

    async def test_a_tenant_cannot_license_another_tenant(
        self, hosted: tuple, priv: Ed25519PrivateKey,
    ) -> None:
        client, vpriv, _app = hosted
        r = await client.post(
            "/v1/license/verify",
            headers={"Authorization": f"Bearer {_tok(priv, 'acme')}"},
            json={"license_json": self._license(vpriv, "globex"), "org": "globex"},
        )
        assert r.status_code == 403
        assert "operator" in r.json()["detail"]

    async def test_naming_a_tenant_does_not_bypass_the_licences_own_name(
        self, hosted: tuple,
    ) -> None:
        """The operator says WHERE it goes; the signed payload still says whose
        it is, and the two must agree."""
        client, vpriv, _app = hosted
        r = await client.post("/v1/license/verify",
                              headers={"Authorization": f"Bearer {TOKEN}"},
                              json={"license_json": self._license(vpriv, "acme"),
                                    "org": "globex"})
        assert r.status_code == 403
        assert "issued to 'acme'" in r.json()["detail"]

    async def test_one_tenants_entitlement_is_not_anothers(
        self, hosted: tuple, priv: Ed25519PrivateKey,
    ) -> None:
        client, vpriv, _app = hosted
        await client.post("/v1/license/verify",
                          headers={"Authorization": f"Bearer {TOKEN}"},
                          json={"license_json": self._license(vpriv, "acme"),
                                "org": "acme"})
        paid = "/v1/regression/history"
        assert (await client.get(paid, headers={
            "Authorization": f"Bearer {_tok(priv, 'acme')}"})).status_code == 200
        assert (await client.get(paid, headers={
            "Authorization": f"Bearer {_tok(priv, 'globex')}"})).status_code == 402

    async def test_a_tenant_still_installs_its_own_without_naming_it(
        self, hosted: tuple, priv: Ed25519PrivateKey,
    ) -> None:
        """The self-serve path is unchanged: no `org` in the body, and the
        caller's own tenant is the answer."""
        client, vpriv, app = hosted
        r = await client.post(
            "/v1/license/verify",
            headers={"Authorization": f"Bearer {_tok(priv, 'acme')}"},
            json={"license_json": self._license(vpriv, "acme")},
        )
        assert r.status_code == 200
        assert r.json()["org"] == "acme"
        assert set(app.state.licenses) == {"acme"}
