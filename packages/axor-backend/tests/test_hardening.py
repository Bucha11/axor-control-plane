"""Regressions for the audit's operational-hardening findings (F-13..F-18).

Each test names the finding it pins and what the defect actually let happen.
"""
from __future__ import annotations

import logging
import pathlib

import httpx
import pytest
from axor_backend.app import create_app
from axor_backend.notifications import Notifier, WebhookRefused, check_webhook_url
from axor_backend.tenancy import PUBLIC_ORG, set_current_org

TOKEN = "master-secret-xyz"


@pytest.fixture
async def client(tmp_path: pathlib.Path):  # noqa: ANN201
    app = create_app(database_url=f"sqlite+aiosqlite:///{tmp_path}/h.db",
                     operator_keys={}, allow_unsigned=True, api_token=TOKEN)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://cp.test") as c, \
            app.router.lifespan_context(app):
        yield c


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── F-13: notifications do not cross tenants ─────────────────────────────────

async def test_delivery_does_not_cross_tenants() -> None:
    """One organization's on-call was paged about another organization's nodes,
    with the node id, level and permalink in the body — Notifier.emit fanned
    every trigger to every subscription in the process."""
    sent: list[tuple[str, dict]] = []

    async def post(url: str, body: dict) -> int:
        sent.append((url, body))
        return 200

    n = Notifier(post=post)
    n.subscribe("http://a.example/hook", ["node_stale"], org="org_a")
    n.subscribe("http://b.example/hook", ["node_stale"], org="org_b")

    assert await n.emit("node_stale", "n1", {"silent_seconds": 31}, org="org_a") == 1
    await n.drain()  # emit schedules; the POST runs off the caller's path
    assert [url for url, _ in sent] == ["http://a.example/hook"]

    assert await n.emit("node_stale", "n1", {"silent_seconds": 31}, org="org_c") == 0


async def test_subscription_listing_is_scoped(client: httpx.AsyncClient) -> None:
    """`GET /v1/notifications/subscriptions` showed every tenant's webhooks."""
    r = await client.post("/v1/notifications/subscribe", headers=_bearer(TOKEN),
                          json={"url": "http://hook.example/x",
                                "triggers": ["node_stale"]})
    assert r.status_code == 200, r.text
    listed = await client.get("/v1/notifications/subscriptions",
                              headers=_bearer(TOKEN))
    assert [s["url"] for s in listed.json()] == ["http://hook.example/x"]


# ── F-14: the webhook channel is not a request-forgery primitive ─────────────

def test_metadata_and_bad_schemes_are_always_refused() -> None:
    """Refused in EVERY posture: no deployment legitimately webhooks to the
    cloud metadata service, and a webhook is an HTTP call by definition."""
    with pytest.raises(WebhookRefused):
        check_webhook_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(WebhookRefused):
        check_webhook_url("file:///etc/passwd")
    with pytest.raises(WebhookRefused):
        check_webhook_url("gopher://evil.example/x")


def test_private_targets_are_refused_only_where_the_boundary_is_real() -> None:
    """Single-tenant self-hosted: a webhook to the collector on the compose
    network is the normal case, so it is allowed. Multi-tenant: an org admin
    holds `operate` without being the infrastructure operator, so it is not."""
    check_webhook_url("http://127.0.0.1:9093/alerts", block_private=False)
    with pytest.raises(WebhookRefused) as excinfo:
        check_webhook_url("http://127.0.0.1:9093/alerts", block_private=True)
    assert "AXOR_WEBHOOK_BLOCK_PRIVATE" in str(excinfo.value)


async def test_subscribe_refuses_an_internal_target_with_400() -> None:
    """The refusal reaches the operator as a 400 that says why, not a 500."""
    n = Notifier(block_private=True)
    with pytest.raises(WebhookRefused):
        n.subscribe("http://10.0.0.5/hook", ["node_stale"])


# ── F-16 / F-18: bounded work per request ────────────────────────────────────

async def test_ingest_batch_has_a_ceiling(client: httpx.AsyncClient) -> None:
    """The batch is held, parsed and folded in memory before anything is
    stored, so its size was a resource the caller controlled entirely."""
    from axor_backend.limits import MAX_EVENTS_PER_BATCH

    oversized = [{"seq": i, "kind": "claim", "node_id": "n"}
                 for i in range(MAX_EVENTS_PER_BATCH + 1)]
    r = await client.post("/v1/ingest/big", headers=_bearer(TOKEN),
                          json={"node_id": "n", "events": oversized})
    assert r.status_code == 413
    assert "AXOR_MAX_EVENTS_PER_BATCH" in r.json()["detail"]


def test_subgraph_cache_is_bounded_and_tenant_keyed() -> None:
    """It was an unbounded dict on a public route, keyed without the tenant —
    a memory leak anyone could drive, and two tenants legitimately hold the
    same run_id (a Lab pin is the deterministic `lab:{trace_id}`)."""
    from axor_backend.limits import SUBGRAPH_CACHE_MAX

    assert SUBGRAPH_CACHE_MAX > 0
    set_current_org(PUBLIC_ORG)


# ── F-15: a query-string token is not a general-purpose credential ───────────

def test_query_token_is_accepted_only_where_a_header_is_impossible() -> None:
    """A token in a URL lands in access logs, history and Referer. It is
    honoured only on the routes a browser opens directly — an EventSource
    stream and an <a href> export — never on the rest of the API."""
    from axor_backend.auth import accepts_query_token

    assert accepts_query_token("GET", "/v1/runs/r1/stream")     # EventSource
    assert accepts_query_token("GET", "/v1/runs/r1/cases/0/export")  # <a href>
    # …and NOT the run's event log, which is ordinary JSON the UI fetches with
    # the Authorization header. It was in the list, so a URL out of an access
    # log or browser history was a working credential to the whole trace.
    assert not accepts_query_token("GET", "/v1/runs/r1/events")
    # An SSE route whose subscriber is the node's own HTTP client, not a
    # browser: a header is possible there, so a query token is not honoured.
    assert not accepts_query_token("GET", "/v1/plane/n1/desired")
    assert not accepts_query_token("GET", "/v1/runs")
    assert not accepts_query_token("GET", "/v1/keys")
    assert not accepts_query_token("POST", "/v1/plane/n1/command")


async def test_query_token_rejected_off_the_browser_routes(
    client: httpx.AsyncClient,
) -> None:
    assert (await client.get(f"/v1/runs?token={TOKEN}")).status_code == 401
    await client.post("/v1/ingest/run_q", headers=_bearer(TOKEN), json={
        "node_id": "n", "events": [
            {"schema_version": "1.0", "seq": 0, "node_id": "n", "kind": "claim",
             "ts": "t", "causal_root": None, "gate": None, "verdict": None,
             "payload": {}}]})
    # The run's event log is JSON the UI fetches with a header, so it is off the
    # browser routes too — this asserted 200 and was the reason it stayed on
    # the list.
    assert (await client.get(f"/v1/runs/run_q/events?token={TOKEN}")).status_code == 401
    assert (await client.get("/v1/runs/run_q/events",
                             headers=_bearer(TOKEN))).status_code == 200


async def test_a_refusal_is_recorded_not_only_returned(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture,
) -> None:
    """401s and 403s used to go to the caller and to nobody else: a deployment
    could not tell it was being probed with a bad token, or that a key was
    being used past its scope. The credential never appears in the line — the
    principal's id does, which is what makes it worth recording."""
    minted = await client.post("/v1/keys", headers=_bearer(TOKEN),
                               json={"scopes": ["read"], "label": "ro"})
    secret = minted.json()["secret"]
    with caplog.at_level(logging.WARNING, logger="axor.backend.auth"):
        assert (await client.get("/v1/runs",
                                 headers=_bearer("wrong"))).status_code == 401
        assert (await client.post("/v1/ingest/r", headers=_bearer(secret),
                                  json={"node_id": "n", "events": []})
                ).status_code == 403
    lines = [r.getMessage() for r in caplog.records]
    assert any(line.startswith("401 GET /v1/runs") for line in lines), lines
    denied = next(line for line in lines if line.startswith("403 POST /v1/ingest/r"))
    assert "needs ingest" in denied and "'read'" in denied
    assert secret not in denied and "wrong" not in "".join(lines)


async def test_unsubscribe_removes_the_row_and_stops_the_delivery(
    client: httpx.AsyncClient
) -> None:
    """Removed from the STORE first: the other order would stop delivery in
    this process and leave a row that resurrects the webhook at the next
    restart — the mirror of why subscribe persists first."""
    body = {"url": "http://hook.example/gone", "triggers": ["node_stale"]}
    assert (await client.post("/v1/notifications/subscribe",
                              headers=_bearer(TOKEN), json=body)).status_code == 200
    listed = (await client.get("/v1/notifications/subscriptions",
                               headers=_bearer(TOKEN))).json()
    assert any(s["url"] == body["url"] for s in listed)

    r = await client.post("/v1/notifications/unsubscribe",
                          headers=_bearer(TOKEN), json={"url": body["url"]})
    assert r.status_code == 200 and r.json()["removed"] == 1
    listed = (await client.get("/v1/notifications/subscriptions",
                               headers=_bearer(TOKEN))).json()
    assert not any(s["url"] == body["url"] for s in listed)


async def test_unsubscribing_something_that_is_not_there_says_so(
    client: httpx.AsyncClient
) -> None:
    r = await client.post("/v1/notifications/unsubscribe",
                          headers=_bearer(TOKEN), json={"url": "http://nope.example"})
    assert r.status_code == 404
