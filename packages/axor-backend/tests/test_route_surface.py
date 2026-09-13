"""The route surface, pinned.

Two failure modes this locks down, both of which have actually happened here:

* **A route quietly disappears.** Splitting a 1300-line factory into routers is
  exactly the kind of change where one handler fails to get registered, and
  nothing else notices until a client 404s in production.
* **A route quietly changes what it costs to call.** Scope is assigned by path
  matching, so adding ``/v1/runs/{id}/something`` silently inherits the
  ``/v1/runs/`` prefix's scope. Two real defects came from that: a node health
  report that required `operate` (unreachable by the only credential a node
  holds) and an influence ranking that required `ingest` while its read-only
  siblings did not.

So this table is deliberately exhaustive and deliberately annoying: a new route
fails this test until somebody writes down what it needs. That is the point —
the decision should be conscious, not inherited from a prefix.

``OPEN`` means served without authentication even when auth is enabled.
"""
from __future__ import annotations

import re

from axor_backend.app import create_app
from axor_backend.auth import is_open, required_scope

# (method, path) -> required scope, or "OPEN" for an unauthenticated route.
EXPECTED: dict[tuple[str, str], str] = {
    ("GET", "/v1/healthz"): "OPEN",
    ("GET", "/v1/auth/status"): "OPEN",
    ("GET", "/v1/share/{token}"): "OPEN",
    # ── run ingest & read ────────────────────────────────────────────────────
    ("POST", "/v1/ingest/{run_id}"): "ingest",
    ("POST", "/v1/runs/{run_id}/evidence"): "ingest",
    ("GET", "/v1/runs"): "read",
    ("GET", "/v1/runs/{run_id}/events"): "read",
    ("GET", "/v1/runs/{run_id}/stream"): "read",
    # ── demo seeds ───────────────────────────────────────────────────────────
    ("POST", "/v1/demo/seed-adapter-runs"): "operate",
    ("POST", "/v1/demo/seed-tree-run"): "operate",
    # ── analysis: pure kernel reads, POST or not ─────────────────────────────
    ("GET", "/v1/runs/{run_id}/subgraph"): "read",
    ("GET", "/v1/runs/{run_id}/containment"): "read",
    ("POST", "/v1/runs/{run_id}/influence"): "read",
    ("GET", "/v1/replay/{run_id}"): "read",
    ("POST", "/v1/replay/{run_id}"): "read",
    # ── vault: split by what each route DOES, not by its shared prefix ───────
    ("POST", "/v1/vault/creds/enroll"): "admin",
    ("POST", "/v1/vault/creds/dispense"): "ingest",
    ("POST", "/v1/vault/creds/rotate"): "admin",
    ("POST", "/v1/vault/creds/revoke"): "operate",
    ("GET", "/v1/vault/creds/health"): "admin",
    # Node pubkeys are config, not secrets — but registering one decides whose
    # attestations verify, so writing is admin and reading sits with health.
    ("POST", "/v1/vault/creds/node-keys"): "admin",
    ("GET", "/v1/vault/creds/node-keys"): "admin",
    # What every dispensed credential was fetched for. Same bar as the signing
    # vault's audit next to it.
    ("GET", "/v1/vault/creds/audit"): "admin",
    # Envelope mode: registering the sealing key is what stops this deployment
    # storing plaintext at all.
    ("POST", "/v1/vault/creds/sealing-key"): "admin",
    ("GET", "/v1/vault/creds/sealing-key"): "admin",
    ("POST", "/v1/vault/signing/keys"): "admin",
    ("GET", "/v1/vault/signing/keys"): "operate",
    ("POST", "/v1/vault/signing/sign"): "operate",
    ("GET", "/v1/vault/signing/audit"): "admin",
    # ── Lab cross-links ──────────────────────────────────────────────────────
    ("GET", "/v1/runs/{run_id}/lab-package"): "read",
    ("POST", "/v1/lab/deploy"): "operate",
    ("GET", "/v1/lab/deploys"): "read",
    # ── regression corpus ────────────────────────────────────────────────────
    ("POST", "/v1/pins/{run_id}"): "ingest",
    ("GET", "/v1/pins"): "read",
    ("POST", "/v1/regression"): "read",
    ("GET", "/v1/regression/history"): "read",
    ("GET", "/v1/regression/schedule"): "read",
    ("PUT", "/v1/regression/schedule"): "operate",
    # ── per-run provenance & attestations ────────────────────────────────────
    ("GET", "/v1/runs/{run_id}/provenance"): "read",
    ("GET", "/v1/runs/{run_id}/attestations"): "read",
    # ── notifications ────────────────────────────────────────────────────────
    ("POST", "/v1/notifications/subscribe"): "operate",
    ("POST", "/v1/notifications/unsubscribe"): "operate",
    ("GET", "/v1/notifications/subscriptions"): "read",
    ("GET", "/v1/notifications/dead-letters"): "read",
    # ── share & export ───────────────────────────────────────────────────────
    ("POST", "/v1/runs/{run_id}/cases/{case_index}/share"): "operate",
    ("DELETE", "/v1/share/{token}"): "operate",
    ("GET", "/v1/runs/{run_id}/cases/{case_index}/export"): "read",
    # ── licensing: /verify WRITES the deployment's entitlement ───────────────
    ("POST", "/v1/license/verify"): "admin",
    ("GET", "/v1/license/status"): "read",
    # Governed-node usage: the tenant's own fleet history, and the basis of any
    # per-node line on their invoice. Their data, so `read` — the same bar as
    # the license status it sits next to.
    ("GET", "/v1/license/usage"): "read",
    # The statement drawn from that usage. Their own bill, so `read` — the same
    # bar as the usage it is computed from.
    ("GET", "/v1/license/invoice"): "read",
    # ── API keys: listing them is credential enumeration ─────────────────────
    ("POST", "/v1/keys"): "admin",
    ("GET", "/v1/keys"): "admin",
    # The listing shows what exists; a revoke erases it from there and not from
    # here. Same scope: reading who was ever issued what is enumeration too.
    ("GET", "/v1/keys/audit"): "admin",
    ("DELETE", "/v1/keys/{key_id}"): "admin",
    # ── plane (protocol v0.3): the node dials OUT; operator commands come in ─
    ("POST", "/v1/plane/{node_id}/command"): "operate",
    ("POST", "/v1/plane/{node_id}/cascade-stop"): "operate",
    ("POST", "/v1/plane/{node_id}/facts"): "operate",
    ("GET", "/v1/plane/{node_id}/desired"): "read",
    ("POST", "/v1/plane/{node_id}/telemetry"): "ingest",
    ("POST", "/v1/plane/{node_id}/consumed"): "ingest",
    ("POST", "/v1/plane/{node_id}/probe-report"): "ingest",
    ("GET", "/v1/plane/{node_id}/probe-report"): "read",
    ("GET", "/v1/plane/{node_id}/coverage"): "read",
    ("GET", "/v1/plane/topology"): "read",
    ("GET", "/v1/plane/nodes"): "read",
    # ── code analysis ────────────────────────────────────────────────────────
    ("POST", "/v1/wrap/scan"): "ingest",
    ("POST", "/v1/wrap/manifests"): "ingest",
}


def _served(app: object) -> set[tuple[str, str]]:
    """Every (method, path) the app answers, walking included routers."""
    found: set[tuple[str, str]] = set()

    def walk(routes: object) -> None:
        for route in routes:  # type: ignore[attr-defined]
            inner = getattr(route, "original_router", None)
            if inner is not None:  # FastAPI wraps include_router()
                walk(inner.routes)
            elif hasattr(route, "routes"):
                walk(route.routes)
            elif hasattr(route, "path"):
                for method in getattr(route, "methods", None) or set():
                    if method != "HEAD":  # Starlette pairs HEAD with every GET
                        found.add((method, route.path))

    walk(app.routes)  # type: ignore[attr-defined]
    return {
        (m, p) for m, p in found
        if not p.startswith(("/docs", "/redoc", "/openapi"))
    }


def _probe(path: str) -> str:
    """A concrete path for the policy matcher — placeholders can't match."""
    return re.sub(r"\{[^}]+\}", "X", path)


def test_route_surface_is_exactly_what_is_declared() -> None:
    served = _served(create_app(database_url="sqlite+aiosqlite:///:memory:"))
    assert served - set(EXPECTED) == set(), "route served but not declared here"
    assert set(EXPECTED) - served == set(), "route declared here but not served"


def test_every_route_requires_the_scope_it_is_supposed_to() -> None:
    for (method, path), expected in sorted(EXPECTED.items()):
        probe = _probe(path)
        if expected == "OPEN":
            assert is_open(method, probe), f"{method} {path} should be open"
            continue
        assert not is_open(method, probe), f"{method} {path} must not be open"
        assert required_scope(method, probe) == expected, (
            f"{method} {path} requires {required_scope(method, probe)!r}, "
            f"expected {expected!r}"
        )
