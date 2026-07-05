"""FastAPI application factory.

The backend persists and fans out; it never interprets governance — that is
the kernel's (repo README). Routers: plane service (protocol v0.2), run
ingest/read, replay + regression corpus.
"""
from __future__ import annotations

import contextlib
import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from axor_core.kernel.replay import replay
from fastapi import FastAPI, Header, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from axor_backend import plane
from axor_backend.broadcast import Broadcast
from axor_backend.replay_api import (
    kernel_config_from_json,
    parse_trace,
    regression_row,
    scrubber_payload,
)
from axor_backend.signing import OperatorKeyring
from axor_backend.storage import Store, init_db, make_engine


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_app(
    database_url: str | None = None,
    operator_keys: dict[str, str] | None = None,
    allow_unsigned: bool | None = None,
) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await init_db(app.state.store.engine)
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

    app.state.store = Store(make_engine(url))
    app.state.broadcast = Broadcast()
    app.state.keyring = OperatorKeyring(keys)
    app.state.allow_unsigned = allow_unsigned

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
        stored = await store.ingest_events(
            run_id, node_id, body.get("events", []), idempotency_key
        )
        for line in body.get("events", []):
            request.app.state.broadcast.publish(
                f"run:{run_id}", {"type": "event", "line": line}
            )
        return {"stored": stored}

    @app.post("/v1/runs/{run_id}/evidence")
    async def set_evidence(run_id: str, body: dict, request: Request) -> dict:
        await request.app.state.store.set_evidence(run_id, body.get("evidence", []))
        return {"ok": True}

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

    @app.get("/v1/healthz")
    async def healthz() -> dict:
        return {"ok": True}

    return app


async def _events_for(store: Store, run_id: str) -> list:
    lines = await store.run_events(run_id)
    if not lines:
        raise HTTPException(404, f"no events for run {run_id}")
    return parse_trace(lines)
