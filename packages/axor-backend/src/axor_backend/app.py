"""FastAPI application factory.

The backend persists and fans out; it never interprets governance — that is
the kernel's (repo README). Routers: plane service (protocol v0.2), run
ingest/read, replay + regression corpus.
"""
from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from axor_core.kernel.replay import replay
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from axor_backend import auth as auth_mod
from axor_backend import plane
from axor_backend.auth import (
    Principal,
    hash_secret,
    is_open,
    master_principal,
    required_scope,
)
from axor_backend.broadcast import Broadcast
from axor_backend.graph import InMemoryGraphStore, register_trace_derivations
from axor_backend.monitor import running_stale_monitor
from axor_backend.notifications import Notifier
from axor_backend.replay_api import (
    kernel_config_from_json,
    parse_trace,
    regression_row,
    scrubber_payload,
)
from axor_backend.share import (
    ShareRegistry,
    evidence_receipt_html,
    evidence_receipt_pdf,
)
from axor_backend.signing import OperatorKeyring
from axor_backend.storage import Store, init_db, make_engine


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_app(
    database_url: str | None = None,
    operator_keys: dict[str, str] | None = None,
    allow_unsigned: bool | None = None,
    api_token: str | None = None,
) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await init_db(app.state.store.engine)
        # The node_stale trigger is edge-detected by a background sweep (spec
        # §16): a silent node emits nothing, so its absence is what we watch.
        async with running_stale_monitor(app):
            yield

    app = FastAPI(title="axor-backend", lifespan=lifespan)
    url = database_url or os.environ.get(
        "AXOR_DATABASE_URL", "sqlite+aiosqlite:///./axor.db"
    )
    keys = operator_keys
    if keys is None:
        keys = json.loads(os.environ.get("AXOR_OPERATOR_KEYS", "{}"))
    if allow_unsigned is None:
        allow_unsigned = os.environ.get("AXOR_ALLOW_UNSIGNED", "") == "1"
    # Auth is enforced iff a master token is configured (env or arg). Unset =
    # open, the same opt-in posture as allow_unsigned (architecture section 9).
    if api_token is None:
        api_token = os.environ.get("AXOR_API_TOKEN") or None

    app.state.store = Store(make_engine(url))
    app.state.broadcast = Broadcast()
    app.state.keyring = OperatorKeyring(keys)
    app.state.allow_unsigned = allow_unsigned
    app.state.notifier = Notifier()
    app.state.shares = ShareRegistry()
    app.state.api_token = api_token
    # Taint/provenance graph (spec decision 6). In-memory by default — the same
    # dev posture as SQLite; a hosted deployment swaps in KuzuGraphStore (per-tenant
    # DB) behind the GraphStore Protocol. Ingested traces fold their arg_refs →
    # value_ref derivations into it, so the graph is real data, not a mock.
    app.state.graph = InMemoryGraphStore()

    async def resolve_principal(request: Request) -> Principal | None:
        """Bearer from the Authorization header, or ?token= for SSE (EventSource
        cannot set headers). Returns the principal, or None if unauthenticated."""
        token = None
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
        if token is None:
            token = request.query_params.get("token")
        if not token:
            return None
        if api_token and auth_mod.constant_time_eq(token, api_token):
            return master_principal()
        # API key: the key_id prefixes the secret (ak_xxx.yyy).
        key_id = token.split(".", 1)[0]
        record = await app.state.store.get_api_key(key_id)
        if record and auth_mod.constant_time_eq(
            record["hashed_secret"], hash_secret(token)
        ):
            return Principal(kind="key", key_id=key_id,
                             scopes=frozenset(record["scopes"]))
        return None

    @app.middleware("http")
    async def auth_gate(request: Request, call_next: Callable) -> Response:
        if api_token is None or is_open(request.url.path):
            return await call_next(request)
        principal = await resolve_principal(request)
        if principal is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        need = required_scope(request.method, request.url.path)
        if not principal.may(need):
            return JSONResponse(
                {"error": "forbidden", "need": need,
                 "have": sorted(principal.scopes)},
                status_code=403,
            )
        request.state.principal = principal
        return await call_next(request)

    app.include_router(plane.router)

    # ── run ingest & read (the proxy's upload path) ───────────────────────────

    @app.post("/v1/ingest/{run_id}", status_code=202)
    async def ingest(
        run_id: str,
        body: dict,
        request: Request,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict:
        store: Store = request.app.state.store
        node_id = body.get("node_id", "proxy")
        await store.upsert_run(run_id, node_id, body.get("scenario", "custom"), _now())
        events = body.get("events", [])
        stored = await store.ingest_events(
            run_id, node_id, events, idempotency_key
        )
        # Fold the trace's value provenance into the taint graph (spec decision 6).
        await register_trace_derivations(request.app.state.graph, run_id, events)
        for line in events:
            request.app.state.broadcast.publish(
                f"run:{run_id}", {"type": "event", "line": line}
            )
        return {"stored": stored}

    @app.post("/v1/runs/{run_id}/evidence")
    async def set_evidence(run_id: str, body: dict, request: Request) -> dict:
        store: Store = request.app.state.store
        evidence = body.get("evidence", [])
        await store.set_evidence(run_id, evidence)
        # Run completed with >=1 EvidenceCase → notify (spec section 16 trigger).
        deviations = [c for c in evidence if c.get("deviation")]
        if deviations:
            # Auto-pin the must-block side here, at the system of record (decision
            # 11): a trace carrying an EvidenceCase IS the regression corpus's
            # block side, and pinning it should not depend on the uploading client
            # remembering to POST /v1/pins. pin() is idempotent, so the proxy's own
            # pin call stays a harmless no-op.
            await store.pin(run_id, "must_block", body.get("scenario", ""))
            await request.app.state.notifier.emit(
                "evidence_run", body.get("node_id", "proxy"),
                {"run_id": run_id, "cases": len(deviations),
                 "permalink": f"/v1/runs/{run_id}"},
            )
        return {"ok": True, "notified": bool(deviations)}

    @app.get("/v1/runs")
    async def list_runs(request: Request) -> list[dict]:
        return await request.app.state.store.list_runs()

    @app.get("/v1/runs/{run_id}/events")
    async def run_events(run_id: str, request: Request) -> list[dict]:
        lines = await request.app.state.store.run_events(run_id)
        return [json.loads(line) for line in lines]

    @app.get("/v1/runs/{run_id}/stream")
    async def run_stream(
        run_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> EventSourceResponse:
        """Live colour-coded audit stream (spec section 8): replay from
        Last-Event-ID (seq), then live events."""
        broadcast: Broadcast = request.app.state.broadcast
        store: Store = request.app.state.store
        queue = broadcast.subscribe(f"run:{run_id}")

        async def stream() -> AsyncIterator[dict]:
            try:
                after = int(last_event_id) if last_event_id else -1
                for line in await store.run_events(run_id, after_seq=after):
                    parsed = json.loads(line)
                    yield {"event": "event", "id": str(parsed["seq"]), "data": line}
                while True:
                    message = await queue.get()
                    line_dict = message["line"]
                    yield {
                        "event": "event",
                        "id": str(line_dict.get("seq", "")),
                        "data": json.dumps(line_dict, sort_keys=True),
                    }
            finally:
                broadcast.unsubscribe(f"run:{run_id}", queue)

        return EventSourceResponse(stream())

    # ── replay & regression (spec section 13) ─────────────────────────────────

    @app.get("/v1/replay/{run_id}")
    async def replay_scrubber(run_id: str, request: Request) -> dict:
        events = await _events_for(request.app.state.store, run_id)
        return scrubber_payload(replay(events))

    @app.post("/v1/replay/{run_id}")
    async def replay_counterfactual(run_id: str, body: dict, request: Request) -> dict:
        events = await _events_for(request.app.state.store, run_id)
        config = kernel_config_from_json(body.get("config", {}))
        return scrubber_payload(replay(events, config))

    @app.post("/v1/pins/{run_id}")
    async def pin_run(run_id: str, body: dict, request: Request) -> dict:
        side = body.get("side")
        if side not in ("must_block", "must_pass"):
            raise HTTPException(400, "side must be must_block|must_pass")
        await request.app.state.store.pin(run_id, side, body.get("label", ""))
        return {"pinned": run_id, "side": side}

    @app.post("/v1/regression")
    async def regression(body: dict, request: Request) -> dict:
        """Config CI over the pinned corpus (decision 11): a corpus needs both
        sides, or a config that blocks everything passes."""
        store: Store = request.app.state.store
        config = kernel_config_from_json(body.get("config", {}))
        rows: list[dict[str, Any]] = []
        for pin in await store.pinned():
            events = await _events_for(store, pin["run_id"])
            rows.append(regression_row(
                pin["run_id"], pin["side"], pin["label"], events, config
            ))
        regressed = sum(1 for r in rows if r["result"] == "regressed")
        escaped = sum(1 for r in rows if r["result"] == "escaped")
        return {
            "rows": rows,
            "regressed": regressed,
            "escaped": escaped,
            "safe_to_ship": regressed == 0 and escaped == 0,
        }

    # ── taint / provenance graph (spec decision 6) ────────────────────────────

    @app.get("/v1/graph/khop")
    async def graph_khop(
        request: Request, focus: str, k: int = 2, limit: int = 100
    ) -> dict:
        """k-hop neighbourhood around a value ref, expand-on-click. Each edge
        carries the run_id it was derived in — the UI links an edge back to that
        run's EvidenceCase."""
        return await request.app.state.graph.khop(focus, k, limit)

    @app.get("/v1/graph/attestations")
    async def graph_attestations(request: Request, ref: str) -> list[dict]:
        """Attestations covering a value branch (spec 8.1.1)."""
        return await request.app.state.graph.branch_attestations(ref)

    # ── notifications (spec section 16) ───────────────────────────────────────

    @app.post("/v1/notifications/subscribe")
    async def notif_subscribe(body: dict, request: Request) -> dict:
        url = body.get("url")
        triggers = body.get("triggers", [])
        if not url or not triggers:
            raise HTTPException(400, "url and triggers required")
        try:
            request.app.state.notifier.subscribe(
                url, triggers, float(body.get("debounce_seconds", 0.0))
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"subscribed": url, "triggers": triggers}

    @app.get("/v1/notifications/dead-letters")
    async def notif_dead_letters(request: Request) -> list[dict]:
        return [
            {"url": d.url, "error": d.error, "attempts": d.attempts,
             "trigger": d.payload.get("trigger")}
            for d in request.app.state.notifier.dead_letters
        ]

    # ── EvidenceCase export & share (spec section 8.3) ────────────────────────

    @app.post("/v1/runs/{run_id}/cases/{case_index}/share")
    async def create_share(run_id: str, case_index: int, request: Request) -> dict:
        link = request.app.state.shares.create(run_id, case_index)
        return {"token": link.token, "url": f"/v1/share/{link.token}"}

    @app.delete("/v1/share/{token}")
    async def revoke_share(token: str, request: Request) -> dict:
        ok = request.app.state.shares.revoke(token)
        if not ok:
            raise HTTPException(404, "unknown token")
        return {"revoked": token}

    @app.get("/v1/share/{token}")
    async def resolve_share(token: str, request: Request) -> HTMLResponse:
        link = request.app.state.shares.resolve(token)
        if link is None:
            raise HTTPException(404, "link revoked or unknown")
        return await _receipt(request, link.run_id, link.case_index)

    @app.get("/v1/runs/{run_id}/cases/{case_index}/export")
    async def export_case(
        run_id: str, case_index: int, request: Request, format: str = "html"
    ) -> Response:
        run = await _case_run(request, run_id, case_index)
        case = run["evidence"][case_index]
        scenario = run.get("scenario", "")
        if format == "pdf":
            pdf = evidence_receipt_pdf(run_id, case, scenario)
            return Response(
                content=pdf, media_type="application/pdf",
                headers={"Content-Disposition":
                         f'attachment; filename="evidence-{run_id}-{case_index}.pdf"'},
            )
        return HTMLResponse(evidence_receipt_html(run_id, case, scenario))

    async def _case_run(request: Request, run_id: str, case_index: int) -> dict:
        store: Store = request.app.state.store
        runs = {r["run_id"]: r for r in await store.list_runs()}
        run = runs.get(run_id)
        if run is None or case_index >= len(run["evidence"]):
            raise HTTPException(404, "no such case")
        return run

    async def _receipt(request: Request, run_id: str, case_index: int) -> HTMLResponse:
        run = await _case_run(request, run_id, case_index)
        return HTMLResponse(evidence_receipt_html(
            run_id, run["evidence"][case_index], run.get("scenario", "")
        ))

    # ── EE license (monetization doc section 4) ───────────────────────────────

    @app.post("/v1/license/verify")
    async def license_verify(body: dict) -> dict:
        from axor_backend.ee.license import LicenseError, verify_license

        vendor_key = body.get("vendor_pubkey") or os.environ.get("AXOR_VENDOR_PUBKEY", "")
        if not vendor_key:
            raise HTTPException(400, "no vendor public key configured")
        try:
            lic = verify_license(body.get("license_json", ""), vendor_key)
        except LicenseError as exc:
            raise HTTPException(403, str(exc)) from exc
        return {
            "org": lic.org, "tier": lic.tier, "node_ceiling": lic.node_ceiling,
            "expiry": lic.expiry, "features": list(lic.features),
        }

    # ── auth: API key management (architecture section 9) ─────────────────────

    @app.get("/v1/auth/status")
    async def auth_status(request: Request) -> dict:
        """Whether auth is on, and (if a token was sent) whether it's valid +
        its scopes. Open so the UI can decide whether to prompt for a token."""
        if api_token is None:
            return {"auth_enabled": False, "authenticated": True,
                    "scopes": sorted(auth_mod.SCOPES)}
        principal = await resolve_principal(request)
        return {
            "auth_enabled": True,
            "authenticated": principal is not None,
            "scopes": sorted(principal.scopes) if principal else [],
        }

    @app.post("/v1/keys", status_code=201)
    async def create_key(body: dict, request: Request) -> dict:
        scopes = body.get("scopes", ["read"])
        bad = set(scopes) - auth_mod.SCOPES
        if bad:
            raise HTTPException(400, f"unknown scopes: {sorted(bad)}")
        key_id, secret = auth_mod.generate_key()
        await request.app.state.store.create_api_key(
            key_id, hash_secret(secret), scopes, body.get("label", ""), _now(),
        )
        # The full secret is returned exactly once; only its hash is stored.
        return {"key_id": key_id, "secret": secret, "scopes": scopes}

    @app.get("/v1/keys")
    async def list_keys(request: Request) -> list[dict]:
        return await request.app.state.store.list_api_keys()

    @app.delete("/v1/keys/{key_id}")
    async def delete_key(key_id: str, request: Request) -> dict:
        ok = await request.app.state.store.delete_api_key(key_id)
        if not ok:
            raise HTTPException(404, "unknown key")
        return {"revoked": key_id}

    @app.get("/v1/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    return app


async def _events_for(store: Store, run_id: str) -> list:
    lines = await store.run_events(run_id)
    if not lines:
        raise HTTPException(404, f"no events for run {run_id}")
    return parse_trace(lines)
