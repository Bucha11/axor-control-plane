"""FastAPI application factory.

The backend persists and fans out; it never interprets governance — that is
the kernel's (repo README). Routers: plane service (protocol v0.2), run
ingest/read, replay + regression corpus.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from axor_core.kernel.replay import replay
from axor_core.kernel.subgraph import causal_subgraph
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from sse_starlette.sse import EventSourceResponse

from axor_backend import auth as auth_mod
from axor_backend import plane, wrap_api
from axor_backend.auth import (
    Principal,
    hash_secret,
    is_open,
    master_principal,
    required_scope,
)
from axor_backend.broadcast import Broadcast
from axor_backend.graph import (
    InMemoryGraphStore,
    register_trace_derivations,
    rehydrate_graph,
)
from axor_backend.monitor import running_stale_monitor
from axor_backend.notifications import Notifier
from axor_backend.replay_api import (
    containment_report,
    influence_ranking,
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
    retention_days: float | None = None,
    vault_creds_token: str | None = None,
    vault_signing_token: str | None = None,
) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Open dev posture must be loud (SECURITY.md): no token means every
        # endpoint is public; unsigned commands mean no operator integrity.
        import logging

        log = logging.getLogger("axor.backend")
        if app.state.api_token is None:
            log.warning(
                "AUTH IS OFF (no AXOR_API_TOKEN) — every endpoint is open. "
                "Fine for localhost, not for a deployment; see SECURITY.md."
            )
        if app.state.allow_unsigned and app.state.keyring.empty:
            log.warning(
                "UNSIGNED PLANE COMMANDS ACCEPTED (AXOR_ALLOW_UNSIGNED=1, no "
                "operator keys) — set AXOR_OPERATOR_KEYS for any real deployment."
            )
        await init_db(app.state.store.engine)
        # Retention (launch-readiness §1): AXOR_RETENTION_DAYS prunes runs
        # older than the window — at boot, then every 6h. Unset = keep forever.
        days = retention_days
        if days is None:
            raw = os.environ.get("AXOR_RETENTION_DAYS", "")
            days = float(raw) if raw else None
        retention_task: asyncio.Task | None = None
        if days is not None and days > 0:
            async def prune_once() -> None:
                cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
                pruned = await app.state.store.prune_runs_older_than(cutoff)
                if pruned:
                    log.info("retention: pruned %d runs older than %s", pruned, cutoff)

            async def prune_loop() -> None:
                while True:
                    await asyncio.sleep(6 * 3600)
                    with contextlib.suppress(Exception):
                        await prune_once()

            await prune_once()
            retention_task = asyncio.create_task(prune_loop())
        # The taint graph is a derived index over the persisted event log —
        # rebuild it from the DB at boot so it survives restarts (and a fresh
        # instance catches up) without a graph database.
        await rehydrate_graph(app.state.store, app.state.graph)
        # Share links and notification subscriptions are primary data: rebuild
        # their in-memory holders from the DB so a restart keeps permalinks live
        # and keeps notifications firing (see storage.share_links / _subs).
        for link in await app.state.store.list_share_links():
            app.state.shares.load(
                link["token"], link["run_id"], link["case_index"], link["revoked"],
            )
        for sub in await app.state.store.list_subscriptions():
            app.state.notifier.subscribe(
                sub["url"], sub["triggers"], sub["debounce_seconds"],
                label=sub.get("label", ""),
                node_pattern=sub.get("node_pattern", "*"),
            )
        # EE license: rehydrate the verified license (env or Settings-pasted)
        # so org features survive a restart; scheduler fires the corpus when
        # due — license re-checked at fire time.
        await _load_license(app)
        schedule_task = asyncio.create_task(_regression_schedule_loop(app))
        # The node_stale trigger is edge-detected by a background sweep (spec
        # §16): a silent node emits nothing, so its absence is what we watch.
        try:
            async with running_stale_monitor(app):
                yield
        finally:
            schedule_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await schedule_task
            if retention_task is not None:
                retention_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await retention_task

    from axor_backend.observability import setup_observability

    setup_observability()
    app = FastAPI(title="axor-backend", lifespan=lifespan)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Launch-day visibility: an unhandled route error is logged with
        # structure (and shipped to Sentry when configured) instead of only
        # surfacing as an opaque 500 in an access log.
        import logging as _logging

        _logging.getLogger("axor.backend").error(
            "unhandled error on %s %s", request.method, request.url.path,
            exc_info=exc,
        )
        return JSONResponse({"error": "internal", "detail": str(exc)}, status_code=500)

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

    _store = Store(make_engine(url))
    app.state.store = _store
    app.state.broadcast = Broadcast()
    app.state.keyring = OperatorKeyring(keys)
    app.state.allow_unsigned = allow_unsigned

    async def _persist_dead_letter(letter: Any) -> None:  # noqa: ANN401 - DeadLetter
        await _store.add_dead_letter(
            letter.url, letter.payload, letter.error, letter.attempts, _now()
        )

    app.state.notifier = Notifier(dead_sink=_persist_dead_letter)
    app.state.license = None  # set by _load_license / a verified paste
    app.state.shares = ShareRegistry()
    app.state.api_token = api_token
    # THE WALL (spec v2 Ch.5 §3): the two vault subsystems are reached with
    # SEPARATE credentials — the ability to dispense tool creds must not grant
    # the ability to request signatures, and vice versa. No shared admin role.
    app.state.vault_creds_token = (
        vault_creds_token or os.environ.get("AXOR_VAULT_CREDS_TOKEN") or None
    )
    app.state.vault_signing_token = (
        vault_signing_token or os.environ.get("AXOR_VAULT_SIGNING_TOKEN") or None
    )
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
    app.include_router(wrap_api.router)

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

    @app.post("/v1/demo/seed-adapter-runs")
    async def seed_adapter_runs(request: Request) -> dict:
        """Ingest two canned adapter-fidelity runs (recorded verdicts + value
        provenance) so counterfactual divergence, the taint graph, and two-sided
        regression can be demonstrated in-app — the proxy cannot produce this
        trace depth. Idempotent: re-seeding overwrites the same run ids."""
        from axor_backend import demo

        store: Store = request.app.state.store
        graph = request.app.state.graph
        for run_id, node, events, evidence, pin_side in (
            ("ex_block", demo.EX_BLOCK_NODE, demo.EX_BLOCK_EVENTS,
             demo.EX_BLOCK_EVIDENCE, "must_block"),
            ("ex_pass", demo.EX_PASS_NODE, demo.EX_PASS_EVENTS, [], "must_pass"),
        ):
            await store.upsert_run(run_id, node, "adapter-demo", _now())
            await store.ingest_events(run_id, node, events, f"seed-{run_id}")
            await register_trace_derivations(graph, run_id, events)
            if evidence:
                await store.set_evidence(run_id, evidence)
            # Pin both corpus sides explicitly (idempotent) — set_evidence's
            # auto-pin lives in the HTTP route, which this seed bypasses.
            await store.pin(run_id, pin_side, "adapter-demo")
        return {"seeded": ["ex_block", "ex_pass"], "config": demo.EX_CONFIG}

    @app.post("/v1/demo/seed-tree-run")
    async def seed_tree_run(request: Request) -> dict:
        """Ingest the canned multi-agent tree run (spec v2): 4 nodes, carried
        taint up two delegation hops, one lateral edge, export denied at the
        orchestrator — the topology graph, the causal subgraph and the
        two-tree containment story all read from this one trace. Idempotent."""
        from axor_backend import demo

        store: Store = request.app.state.store
        graph = request.app.state.graph
        await store.upsert_run("ex_tree", demo.TREE_ORCH, "multi-agent-demo", _now())
        await store.ingest_events(
            "ex_tree", demo.TREE_ORCH, demo.TREE_EVENTS, "seed-ex_tree"
        )
        await register_trace_derivations(graph, "ex_tree", demo.TREE_EVENTS)
        await store.set_evidence("ex_tree", demo.TREE_EVIDENCE)
        return {"seeded": ["ex_tree"], "config": demo.TREE_CONFIG}

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
                # A malformed Last-Event-ID must not kill the stream — replay all.
                try:
                    after = int(last_event_id) if last_event_id else -1
                except ValueError:
                    after = -1
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

    # Derive-on-open cache (decision v2-12): keyed by (run_id, anchor), never
    # invalidated — traces are append-only.
    _subgraph_cache: dict[tuple, dict] = {}

    @app.get("/v1/runs/{run_id}/subgraph")
    async def run_subgraph(
        run_id: str, anchor_node: str, anchor_seq: int, request: Request
    ) -> dict:
        """The causal subgraph for a case (spec v2 Ch.3): computed on open by
        the pure kernel walk, cached, never stored redundantly."""
        key = (run_id, anchor_node, anchor_seq)
        if key not in _subgraph_cache:
            events = await _events_for(request.app.state.store, run_id)
            try:
                _subgraph_cache[key] = causal_subgraph(
                    events, anchor_node, anchor_seq
                )
            except ValueError as exc:
                raise HTTPException(404, str(exc)) from exc
        return _subgraph_cache[key]

    @app.get("/v1/runs/{run_id}/containment")
    async def run_containment(
        run_id: str, anchor_node: str, anchor_seq: int, request: Request
    ) -> dict:
        """Containment metric + systemic outcome for a case (spec v2 Ch.2):
        event-grounded (headline-safe) ratio, outcome as a label — never a
        governance-attributed score."""
        events = await _events_for(request.app.state.store, run_id)
        try:
            sub = causal_subgraph(events, anchor_node, anchor_seq)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return containment_report(events, sub)

    @app.post("/v1/runs/{run_id}/influence")
    async def run_influence(run_id: str, body: dict, request: Request) -> dict:
        """Influence ranking by subgraph ablation (spec v2 Ch.3 §7): which
        upstream value most drove the anchor's claim. Deterministic; bounded
        by causal-chain length."""
        anchor_node = body.get("anchor_node", "")
        anchor_seq = int(body.get("anchor_seq", -1))
        events = await _events_for(request.app.state.store, run_id)
        try:
            sub = causal_subgraph(events, anchor_node, anchor_seq)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        case_nodes = {n["node_id"] for n in sub["nodes"]}
        refs = sorted({
            str(e.payload.get("value_ref"))
            for e in events
            if e.node_id in case_nodes and e.payload.get("value_ref")
        })
        cfg_json = body.get("config") or {}
        if not cfg_json:
            # Default ablation config: the anchor's own denied sink declared as
            # egress — the minimal config under which the recorded containment
            # reproduces, so ablation measures exactly "did this value drive
            # the denial".
            anchor_ev = next(
                e for e in events
                if e.node_id == anchor_node and e.seq == anchor_seq
            )
            tool = str(anchor_ev.payload.get("tool", ""))
            cfg_json = {"egress_sinks": [tool] if tool else []}
        config = kernel_config_from_json(cfg_json)
        return {
            "anchor": sub["anchor"],
            "ranking": influence_ranking(
                events, config, anchor_node, anchor_seq, refs
            ),
        }

    # ── Federation vault (spec v2 Ch.5): two subsystems, one wall ────────────

    def _vault_gate(request: Request, which: str) -> None:
        """Per-subsystem bearer check. A configured token is required exactly
        for its own subsystem; in the open dev posture (no token configured)
        the subsystem follows the app's global posture."""
        expected = getattr(request.app.state, f"vault_{which}_token")
        if expected is None:
            return  # open posture — loud in logs at startup, like api_token
        got = request.headers.get(f"x-vault-{which}-token", "")
        if got != expected:
            raise HTTPException(403, f"vault {which}: missing or wrong token")

    @app.post("/v1/vault/creds/enroll")
    async def vault_enroll(body: dict, request: Request) -> dict:
        _vault_gate(request, "creds")
        from axor_backend.vault_creds import ToolCredentialVault

        vault = ToolCredentialVault(request.app.state.store)
        return await vault.enroll(
            str(body.get("tool", "")), str(body.get("endpoint", "")),
            str(body.get("secret", "")), list(body.get("scope_nodes", [])),
        )

    @app.post("/v1/vault/creds/dispense")
    async def vault_dispense(body: dict, request: Request) -> dict:
        _vault_gate(request, "creds")
        from axor_backend.vault_creds import DispenseDenied, ToolCredentialVault

        vault = ToolCredentialVault(request.app.state.store)
        try:
            return await vault.dispense(
                str(body.get("node_id", "")), str(body.get("tool", "")),
                str(body.get("endpoint", "")),
            )
        except DispenseDenied as exc:
            raise HTTPException(403, exc.reason) from exc

    @app.post("/v1/vault/creds/rotate")
    async def vault_rotate(body: dict, request: Request) -> dict:
        _vault_gate(request, "creds")
        from axor_backend.vault_creds import DispenseDenied, ToolCredentialVault

        vault = ToolCredentialVault(request.app.state.store)
        try:
            return await vault.rotate(
                str(body.get("tool", "")), str(body.get("endpoint", "")),
                str(body.get("secret", "")),
            )
        except DispenseDenied as exc:
            raise HTTPException(404, exc.reason) from exc

    @app.post("/v1/vault/creds/revoke")
    async def vault_revoke(body: dict, request: Request) -> dict:
        """Narrowing only: revoke is available over the plane in an incident;
        granting is enrollment config, never a command."""
        _vault_gate(request, "creds")
        from axor_backend.vault_creds import DispenseDenied, ToolCredentialVault

        vault = ToolCredentialVault(request.app.state.store)
        try:
            return await vault.revoke(
                str(body.get("tool", "")), str(body.get("endpoint", "")),
            )
        except DispenseDenied as exc:
            raise HTTPException(404, exc.reason) from exc

    @app.get("/v1/vault/creds/health")
    async def vault_health(request: Request) -> dict:
        _vault_gate(request, "creds")
        from axor_backend.vault_creds import ToolCredentialVault

        return await ToolCredentialVault(request.app.state.store).health()

    @app.post("/v1/vault/signing/keys")
    async def vault_create_key(body: dict, request: Request) -> dict:
        _vault_gate(request, "signing")
        from axor_backend.vault_signing import SigningCustody, SignRefused

        custody = SigningCustody(request.app.state.store)
        try:
            return await custody.create_key(
                str(body.get("key_id", "")), list(body.get("operators", [])),
            )
        except SignRefused as exc:
            raise HTTPException(409, exc.reason) from exc

    @app.get("/v1/vault/signing/keys")
    async def vault_list_keys(request: Request) -> list[dict]:
        _vault_gate(request, "signing")
        from axor_backend.vault_signing import SigningCustody

        return await SigningCustody(request.app.state.store).keys_public()

    @app.post("/v1/vault/signing/sign")
    async def vault_sign(body: dict, request: Request) -> dict:
        _vault_gate(request, "signing")
        import base64

        from axor_backend.vault_signing import SigningCustody, SignRefused

        custody = SigningCustody(request.app.state.store)
        try:
            return await custody.sign(
                str(body.get("operator", "")), str(body.get("key_id", "")),
                base64.b64decode(str(body.get("payload_b64", ""))),
            )
        except SignRefused as exc:
            raise HTTPException(403, exc.reason) from exc

    @app.get("/v1/vault/signing/audit")
    async def vault_audit(request: Request) -> list[dict]:
        _vault_gate(request, "signing")
        from axor_backend.vault_signing import SigningCustody

        return await SigningCustody(request.app.state.store).audit()

    @app.get("/v1/replay/{run_id}")
    async def replay_scrubber(run_id: str, request: Request) -> dict:
        events = await _events_for(request.app.state.store, run_id)
        return scrubber_payload(replay(events))

    @app.post("/v1/replay/{run_id}")
    async def replay_counterfactual(run_id: str, body: dict, request: Request) -> dict:
        events = await _events_for(request.app.state.store, run_id)
        config = kernel_config_from_json(body.get("config", {}))
        return scrubber_payload(replay(events, config))

    # ── Axor Lab cross-links (CP → Lab incident export, Lab → CP deploy) ─────

    @app.get("/v1/runs/{run_id}/lab-package")
    async def run_lab_package(run_id: str, request: Request) -> dict:
        """The run as an axor-lab-incident/v1 package (trace + scenario +
        manifests + recorded condition) — the input of `axor-lab
        import-incident`. 422 with the full reason list when the run is not
        convertible (proxy-depth events, unreproducible verdicts, no vector)."""
        from axor_backend.lab_export import LabExportError, build_incident_package

        store: Store = request.app.state.store
        runs = {r["run_id"]: r for r in await store.list_runs()}
        run = runs.get(run_id)
        if run is None:
            raise HTTPException(404, f"no such run {run_id}")
        events = [json.loads(line) for line in await store.run_events(run_id)]
        try:
            return build_incident_package(events, run)
        except LabExportError as exc:
            raise HTTPException(
                422, {"error": "run is not convertible to a Lab incident package",
                      "reasons": list(exc.reasons)},
            ) from exc

    @app.post("/v1/lab/deploy")
    async def lab_deploy(body: dict, request: Request) -> dict:
        """Accept a cp-deploy.json produced by `axor-lab export-cp`: validate
        (finalized evidence-backed packages only), store the package record,
        and fold its regression pins into the corpus with source lab:{id}."""
        from axor_backend.lab_import import (
            deploy_plans,
            package_id_of,
            validate_cp_deploy,
        )

        problems = validate_cp_deploy(body)
        if problems:
            raise HTTPException(
                422, {"error": "cp-deploy package rejected", "reasons": problems},
            )
        store: Store = request.app.state.store
        package_id = package_id_of(body)
        plans = deploy_plans(body, package_id)
        stored_new = await store.add_lab_deploy(package_id, body, len(plans), _now())
        for plan in plans:  # idempotent per run_id — a re-upload re-asserts them
            await store.pin(plan.run_id, plan.side, plan.label)
            # a pin whose carried trace was recorded under the real axor-core
            # kernel and reproduces here is stored as replayable corpus events —
            # the regression report folds them instead of skipping the pin
            if plan.replayable:
                await store.add_lab_trace_events(plan.run_id, plan.event_lines)
        return {
            "package_id": package_id,
            "pins_created": len(plans),
            # how many Lab pins are now REPLAYABLE corpus traces (real-kernel,
            # build-matched, verdict reproduces) vs left skipped with a reason
            "pins_replayable": sum(1 for p in plans if p.replayable),
            "pins_skipped": [
                {"run_id": p.run_id, "trace_id": p.trace_id, "reason": p.reason}
                for p in plans if not p.replayable
            ],
            "policy_stored": True,
            "already_deployed": not stored_new,
        }

    @app.get("/v1/lab/deploys")
    async def lab_deploys_list(request: Request) -> list[dict]:
        return await request.app.state.store.list_lab_deploys()

    @app.post("/v1/pins/{run_id}")
    async def pin_run(run_id: str, body: dict, request: Request) -> dict:
        side = body.get("side")
        if side not in ("must_block", "must_pass"):
            raise HTTPException(400, "side must be must_block|must_pass")
        await request.app.state.store.pin(run_id, side, body.get("label", ""))
        return {"pinned": run_id, "side": side}

    @app.get("/v1/pins")
    async def list_pins(request: Request) -> dict:
        """The regression corpus as a flat list + side counts. This is the
        North-star surface: how many caught EvidenceCases the operator committed
        to permanent checks (must_block auto-pins on evidence, must_pass by
        hand). A growing corpus means Axor's findings were trusted enough to
        guard against forever — the signal that the loop closed."""
        pins = await request.app.state.store.pinned()
        must_block = sum(1 for p in pins if p["side"] == "must_block")
        must_pass = sum(1 for p in pins if p["side"] == "must_pass")
        return {"pins": pins, "must_block": must_block,
                "must_pass": must_pass, "total": len(pins)}

    @app.post("/v1/regression")
    async def regression(body: dict, request: Request) -> dict:
        """Config CI over the pinned corpus (decision 11): a corpus needs both
        sides, or a config that blocks everything passes. Manual runs are free
        forever (Line 1); every run leaves a history row and a failing corpus
        fires the regression_failed trigger."""
        report = await _regression_report(
            request.app.state.store, body.get("config", {})
        )
        await _record_corpus_run(request.app, report, "manual")
        return report

    @app.get("/v1/regression/history")
    async def regression_history(request: Request, limit: int = 50) -> list[dict]:
        """Corpus-run history — the org surface (EE): "when did this config
        last regress"."""
        _require_ee(request.app, "regression history")
        return await request.app.state.store.list_regression_reports(limit)

    @app.get("/v1/regression/schedule")
    async def get_regression_schedule(request: Request) -> dict:
        """Free to read (the UI shows the locked state); writing needs EE."""
        sched = await request.app.state.store.get_setting("regression_schedule")
        return {
            "enabled": bool(sched and sched.get("enabled")),
            "interval_hours": (sched or {}).get("interval_hours"),
            "last_run_ts": (sched or {}).get("last_run_ts"),
            "ee_active": _active_license(request.app) is not None,
        }

    @app.put("/v1/regression/schedule")
    async def put_regression_schedule(body: dict, request: Request) -> dict:
        """Scheduled corpus CI (EE): store {enabled, interval_hours, config};
        the sweep loop fires it when due and regression_failed gets loud."""
        _require_ee(request.app, "scheduled corpus CI")
        enabled = bool(body.get("enabled"))
        try:
            interval = float(body.get("interval_hours", 24))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "interval_hours must be a number") from exc
        if enabled and interval < 1:
            raise HTTPException(400, "interval_hours must be >= 1")
        config = body.get("config", {})
        if not isinstance(config, dict):
            raise HTTPException(400, "config must be an object")
        # Setting a schedule validates the config now, not at 3am.
        kernel_config_from_json(config)
        prior = await request.app.state.store.get_setting("regression_schedule")
        sched = {
            "enabled": enabled, "interval_hours": interval, "config": config,
            "last_run_ts": (prior or {}).get("last_run_ts"),
        }
        await request.app.state.store.set_setting("regression_schedule", sched)
        return {"enabled": enabled, "interval_hours": interval}

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
            debounce = float(body.get("debounce_seconds", 0.0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "debounce_seconds must be a number") from exc
        # Routing (channels + node globs) is the org layer (EE); the free
        # shape — one plain webhook catching everything — stays free forever.
        label = str(body.get("label") or "")
        node_pattern = str(body.get("node_pattern") or "*")
        if label or node_pattern != "*":
            _require_ee(request.app, "notification routing (channels / node patterns)")
        try:
            request.app.state.notifier.subscribe(
                url, triggers, debounce, label=label, node_pattern=node_pattern
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        # Persist so the subscription survives a restart (idempotent on
        # url+triggers+pattern, so the boot rehydrate never double-registers).
        await request.app.state.store.add_subscription(
            url, triggers, debounce, label=label, node_pattern=node_pattern
        )
        return {"subscribed": url, "triggers": triggers,
                "label": label, "node_pattern": node_pattern}

    @app.get("/v1/notifications/subscriptions")
    async def notif_subscriptions(request: Request) -> list[dict]:
        return await request.app.state.store.list_subscriptions()

    @app.get("/v1/notifications/dead-letters")
    async def notif_dead_letters(request: Request) -> list[dict]:
        # Read-through from the store: dead letters persist across restarts
        # (migration 0002) — the log of lost deliveries must not itself be lossy.
        rows = await request.app.state.store.list_dead_letters()
        return [
            {"url": d["url"], "error": d["error"], "attempts": d["attempts"],
             "trigger": (d["payload"] or {}).get("trigger"),
             "created_ts": d["created_ts"]}
            for d in rows
        ]

    # ── EvidenceCase export & share (spec section 8.3) ────────────────────────

    @app.post("/v1/runs/{run_id}/cases/{case_index}/share")
    async def create_share(run_id: str, case_index: int, request: Request) -> dict:
        await _case_run(request, run_id, case_index)  # 404 before minting a dead token
        link = request.app.state.shares.create(run_id, case_index)
        await request.app.state.store.create_share_link(
            link.token, run_id, case_index, _now()
        )
        return {"token": link.token, "url": f"/v1/share/{link.token}"}

    @app.delete("/v1/share/{token}")
    async def revoke_share(token: str, request: Request) -> dict:
        ok = request.app.state.shares.revoke(token)
        if not ok:
            raise HTTPException(404, "unknown token")
        await request.app.state.store.revoke_share_link(token)
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
    async def license_verify(body: dict, request: Request) -> dict:
        from axor_backend.ee.license import LicenseError, verify_license

        vendor_key = body.get("vendor_pubkey") or os.environ.get("AXOR_VENDOR_PUBKEY", "")
        if not vendor_key:
            raise HTTPException(400, "no vendor public key configured")
        try:
            lic = verify_license(body.get("license_json", ""), vendor_key)
        except LicenseError as exc:
            raise HTTPException(403, str(exc)) from exc
        # A verified license ACTIVATES EE: persist it (survives restarts) and
        # hold it in state so org features unlock immediately. Only licenses
        # verified against the operator-pinned vendor key ever get stored.
        if not body.get("vendor_pubkey") or body.get("vendor_pubkey") == os.environ.get(
            "AXOR_VENDOR_PUBKEY", ""
        ):
            await request.app.state.store.set_setting(
                "license_json", body.get("license_json", "")
            )
            request.app.state.license = lic
        # Node-ceiling telemetry (launch-readiness §5): compare the live fleet
        # against the license and WARN — never block; safety never checks a
        # license (monetization Line 1).
        live_nodes = len(await request.app.state.store.list_nodes())
        from axor_backend.ee.license import KNOWN_MODULES
        return {
            "organization": lic.organization,
            "workspace_tier": lic.workspace_tier,
            "modules": {m: lic.has_module(m) for m in KNOWN_MODULES},
            "governed_node_ceiling": lic.governed_node_ceiling,
            "self_hosted_runner": lic.self_hosted_runner,
            "expires_at": lic.expires_at,
            "features": list(lic.features),
            "live_nodes": live_nodes,
            "over_ceiling": live_nodes > lic.governed_node_ceiling,
            "activated": request.app.state.license is lic,
        }

    @app.get("/v1/license/status")
    async def license_status(request: Request) -> dict:
        """The currently ACTIVE license (post-boot rehydrate) — what the UI
        uses to decide which org features to unlock vs render locked."""
        lic = _active_license(request.app)
        if lic is None:
            return {"active": False}
        from axor_backend.ee.license import KNOWN_MODULES
        return {
            "active": True,
            "organization": lic.organization,
            "workspace_tier": lic.workspace_tier,
            "modules": {m: lic.has_module(m) for m in KNOWN_MODULES},
            "governed_node_ceiling": lic.governed_node_ceiling,
            "self_hosted_runner": lic.self_hosted_runner,
            "expires_at": lic.expires_at,
            "features": list(lic.features),
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
        if not scopes:
            raise HTTPException(400, "scopes must be non-empty")
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


async def _regression_report(store: Store, config_json: dict) -> dict:
    """Run the pinned corpus under a config — shared by the manual route and
    the EE scheduler, so both produce identical reports."""
    config = kernel_config_from_json(config_json)
    rows: list[dict[str, Any]] = []
    skipped: list[str] = []
    for pin in await store.pinned():
        # A pin whose run has no replayable kernel trace (deleted events,
        # telemetry-only) must not 4xx the whole report — skip it and say so.
        try:
            events = await _events_for(store, pin["run_id"])
        except HTTPException:
            skipped.append(pin["run_id"])
            continue
        rows.append(regression_row(
            pin["run_id"], pin["side"], pin["label"], events, config
        ))
    regressed = sum(1 for r in rows if r["result"] == "regressed")
    escaped = sum(1 for r in rows if r["result"] == "escaped")
    return {
        "rows": rows,
        "regressed": regressed,
        "escaped": escaped,
        "skipped": skipped,
        "safe_to_ship": regressed == 0 and escaped == 0,
    }


async def _record_corpus_run(app: FastAPI, report: dict, source: str) -> None:
    """Every corpus run leaves history; a failing one gets loud (spec §16)."""
    await app.state.store.add_regression_report(report, source, _now())
    if report["regressed"] or report["escaped"]:
        await app.state.notifier.emit("regression_failed", "corpus", {
            "source": source,
            "regressed": report["regressed"],
            "escaped": report["escaped"],
            "total": len(report["rows"]),
        })


# ── EE license state (monetization §4): verified once, persisted, honest ─────

def _verify_license_str(license_json: str) -> Any:  # noqa: ANN401 - License
    from axor_backend.ee.license import verify_license

    vendor_key = os.environ.get("AXOR_VENDOR_PUBKEY", "")
    if not vendor_key:
        raise ValueError("no AXOR_VENDOR_PUBKEY configured")
    return verify_license(license_json, vendor_key)


async def _load_license(app: FastAPI) -> None:
    """Boot rehydrate: AXOR_LICENSE env (raw license-file JSON) wins, else the
    license pasted in Settings (persisted in the settings KV). Invalid or
    unverifiable licenses log a warning and leave EE off — never crash boot."""
    import logging

    raw = os.environ.get("AXOR_LICENSE") or await app.state.store.get_setting(
        "license_json"
    )
    if not raw:
        return
    try:
        app.state.license = _verify_license_str(raw)
    except Exception as exc:  # noqa: BLE001 - boot must not die on a bad license
        logging.getLogger("axor.backend").warning("stored license ignored: %s", exc)


def _active_license(app: FastAPI) -> Any | None:  # noqa: ANN401 - License
    """The verified, non-expired license — or None. Expiry degrades EE to
    read-only (Line 1: safety never checks a license)."""
    lic = getattr(app.state, "license", None)
    if lic is None or lic.is_expired(datetime.now(UTC).date().isoformat()):
        return None
    return lic


def _require_ee(
    app: FastAPI, what: str, *, min_tier: str = "team", module: str | None = None
) -> None:
    """Gate a paid org feature by the license's workspace tier (and optionally a
    module), not merely by a license being present (axor-packaging.md §1). A
    community-tier license does not unlock a team feature; the 402 names what is
    needed. Safety features never call this."""
    lic = _active_license(app)
    if lic is None:
        raise HTTPException(
            402,
            f"{what} is a paid org feature ({min_tier} tier) — add a license in "
            "Settings → LICENSE. Safety features never require one.",
        )
    if not lic.tier_at_least(min_tier):
        raise HTTPException(
            402,
            f"{what} needs the {min_tier} workspace tier or higher; this license is "
            f"'{lic.workspace_tier}'.",
        )
    if module is not None and not lic.has_module(module):
        raise HTTPException(
            402, f"{what} needs the {module} module, which this license does not enable."
        )


async def _regression_schedule_loop(app: FastAPI) -> None:
    """EE scheduler sweep: fire the corpus when the operator-set interval is
    due. License is checked at fire time — an expired license pauses the
    schedule (EE read-only) without touching the stored setting."""
    import logging

    log = logging.getLogger("axor.backend")
    sweep = float(os.environ.get("AXOR_SCHEDULE_SWEEP_SECONDS", "60"))
    while True:
        await asyncio.sleep(sweep)
        try:
            sched = await app.state.store.get_setting("regression_schedule")
            if not sched or not sched.get("enabled"):
                continue
            if _active_license(app) is None:
                continue
            last = sched.get("last_run_ts")
            interval = timedelta(hours=float(sched.get("interval_hours", 24)))
            now = datetime.now(UTC)
            if last is not None and now - datetime.fromisoformat(last) < interval:
                continue
            report = await _regression_report(
                app.state.store, sched.get("config", {})
            )
            await _record_corpus_run(app, report, "scheduled")
            sched["last_run_ts"] = now.isoformat()
            await app.state.store.set_setting("regression_schedule", sched)
            log.info(
                "scheduled corpus run: %d rows, regressed=%d escaped=%d",
                len(report["rows"]), report["regressed"], report["escaped"],
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - the loop must survive a bad cycle
            log.exception("scheduled corpus run failed")


async def _events_for(store: Store, run_id: str) -> list:
    lines = await store.run_events(run_id)
    if not lines:
        raise HTTPException(404, f"no events for run {run_id}")
    # A run's stored lines can mix kernel-schema trace events with plane
    # telemetry (heartbeats carry no schema_version) — a governed node's
    # keepalive run is even heartbeat-only. Replay is defined over the kernel
    # trace, so drop the telemetry lines and be honest when nothing remains,
    # instead of letting the kernel's SchemaVersionError surface as a 500.
    kernel_lines = [ln for ln in lines if json.loads(ln).get("schema_version")]
    if not kernel_lines:
        raise HTTPException(
            422, f"run {run_id} has no kernel-schema events to replay "
            "(plane telemetry only — e.g. heartbeats)",
        )
    try:
        return parse_trace(kernel_lines)
    except Exception as exc:  # kernel parse errors are client data errors here
        raise HTTPException(422, f"run {run_id} is not a replayable kernel trace: {exc}") from exc
