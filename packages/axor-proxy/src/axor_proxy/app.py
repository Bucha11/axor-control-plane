"""The observe-only passthrough proxy (spec section 6).

Rules, in order of importance:
- Auth is passthrough, byte-for-byte: the Authorization header (and everything
  else except hop-by-hop headers) is forwarded untouched, never parsed, never
  stored. The ONE exception is vault mode (ui-spec §14.2), opt-in per tool via
  AXOR_VAULT_TOOLS: for those tools and no others the proxy fetches the
  credential at call time and injects it at the sink, so the agent never holds
  it. §14.2 names that reversal and its price; `axor_proxy.vault` carries the
  reasoning and the bounds.
- Exactly two intervention points: inject fault (armed scenario), record
  observation. Everything else is clean passthrough.
- Observe-only: the proxy never blocks the agent. A run armed WITH tool
  manifests additionally carries an `axor_core.governor.ToolCallGovernor`, so
  each call it can see the arguments of is evaluated and its verdict recorded —
  the posture axor-wrap spells `enforcement="off"`, and the same governor. The
  HTTP boundary is the one integration that does not reduce to that flag on a
  `WrappedToolset`: there is no callable here to wrap. Without manifests
  nothing is governed and the trace keeps exactly the shape it had — the effect
  class of a tool is the operator's declaration, never inferred from a name.
- No raw bodies persisted: observations carry status, sizes and hashes only.
  Call arguments go to the governor and never to the trace; the verdict does.
- Disarmed endpoints return 503 (decision #1): traffic flows only while a run
  is armed.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from axor_core.kernel.events import EventKind, Verdict
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route

from axor_proxy.agent import ScriptedAgent, default_claim, default_script
from axor_proxy.mcp import McpError, discover, sniff_rpc_call
from axor_proxy.mock_tools import mock_tools_app
from axor_proxy.runs import (
    VAULT_DENIAL_CATEGORY,
    Run,
    RunManager,
    evidence_to_dict,
    sha256_hex,
)
from axor_proxy.stdio_mcp import StdioMcpServer, discover_stdio
from axor_proxy.upload import BackendUploader
from axor_proxy.vault import CredentialDenied, CredentialVault, vault_tools

# Hop-by-hop headers never forwarded in either direction (RFC 9110 s7.6.1).
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host", "content-length",
})


def _forward_headers(headers: Headers) -> list[tuple[bytes, bytes]]:
    """Raw header pairs minus hop-by-hop — Authorization passes byte-for-byte."""
    return [
        (k, v) for k, v in headers.raw if k.decode().lower() not in _HOP_BY_HOP
    ]


def _gate(category: str) -> str:
    """A denial category -> the kernel's own gate name.

    `axor_core.governor` exports GATE_OF_CATEGORY precisely so consumers stop
    keeping private maps; an unknown category is a loud error rather than a
    category quietly leaking into a column that takes a gate name.
    """
    from axor_core.governor import gate_of

    return gate_of(category)


def _govern(
    run: Run, tool: str, args: dict[str, Any] | None, payload: dict[str, Any],
) -> tuple[object | None, str | None, Verdict | None]:
    """Evaluate one call against the run's governor, if it has one.

    Observe-only: the proxy's third rule is that it never blocks the agent, so a
    deny is RECORDED and the call proceeds — the same posture `axor-wrap` calls
    ``enforcement="off"``, and the same governor underneath. What the HTTP
    boundary cannot supply is a callable to wrap; the decision is identical.

    Returns (decision, gate, verdict). A run with no governor, or a call whose
    arguments are not observable, gets (None, None, None) and says which in the
    payload — an unlabelled call must not read as an approved one.
    """
    if run.governor is None:
        return None, None, None
    if args is None:
        payload["governance"] = {
            "governed": False,
            "reason": "arguments not observable at the HTTP boundary: only an "
                      "MCP tools/call body names them",
        }
        return None, None, None
    decision = run.governor.evaluate(tool, args)  # type: ignore[attr-defined]
    payload["governance"] = {"governed": True, "enforcement": "off"}
    if decision.allowed:
        return decision, None, Verdict.PASS
    payload["reason"] = decision.reason
    payload["category"] = decision.category
    return decision, _gate(decision.category), Verdict.DENY


def hasher_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_stream(content_type: str) -> bool:
    """An event-stream is relayed whatever the run is: buffering one to feed the
    ledger would stall the tool it exists to carry."""
    return content_type.split(";")[0].strip().lower() == "text/event-stream"


def _rpc_value(body: Any) -> Any:  # noqa: ANN401 — arbitrary tool JSON
    """The value a JSON-RPC answer actually carries.

    A `tools/call` response wraps its result in `{"jsonrpc", "id", "result"}`;
    registering the envelope would put a ref on a dict the next call never
    passes, so the taint edge would never fold. Non-RPC bodies pass through.
    """
    if isinstance(body, dict) and body.get("jsonrpc") == "2.0" and "result" in body:
        return body["result"]
    return body


def _ledger(entry: dict[str, Any]) -> dict[str, Any]:
    """`{"ledger": entry}`, or nothing at all for an ungoverned run — an
    ungoverned trace keeps exactly the shape it had."""
    return {"ledger": entry} if entry else {}


def _register(run: Run, decision: object | None, output: object) -> dict[str, Any]:
    """Fold an executed call's output into the run's per-value taint ledger.

    Only for calls that actually ran, and only where the proxy holds the value.
    The returned dict goes on the TOOL_RESULT so a reader can tell a ledger
    entry from a gap: a later egress call that passes under a ledger which never
    saw the upstream body has passed on incomplete evidence, and that has to be
    legible rather than implied.
    """
    if run.governor is None:
        return {}
    if decision is None:
        return {"registered": False, "reason": "call was not governed"}
    run.governor.register_output(decision, output)  # type: ignore[attr-defined]
    for event in run.governor.drain_trace_events():  # type: ignore[attr-defined]
        ref = (getattr(event, "payload", None) or {}).get("value_ref")
        if ref:
            return {"registered": True, "value_ref": ref}
    return {"registered": True}


class ProxyState:
    def __init__(
        self,
        tools: dict[str, str],
        trace_dir: Path,
        client: httpx.AsyncClient | None = None,
        backend_url: str | None = None,
        uploader: BackendUploader | None = None,
        self_base_url: str = "http://127.0.0.1:8401",
        agent_client: httpx.AsyncClient | None = None,
        ingest_key: str | None = None,
        control_token: str | None = None,
        vault: CredentialVault | None = None,
        vault_tool_names: frozenset[str] | None = None,
    ) -> None:
        import os as _os

        # Control-surface token (AXOR_PROXY_TOKEN). The proxy arms runs, injects
        # faults into live tool traffic, spawns governed nodes and reads back
        # traces — and docker-compose publishes its port. Unset keeps the
        # historical open posture for a loopback test bench; set it and every
        # /axor route needs the bearer. The /t/ passthrough is deliberately NOT
        # gated: the agent's own credential rides through it byte-for-byte and
        # the run must already be armed for anything to happen.
        self.control_token = (
            control_token or _os.environ.get("AXOR_PROXY_TOKEN") or None
        )

        # tool name -> upstream: an HTTP base url, or a live StdioMcpServer
        # (the stdio-MCP gateway) registered via /axor/mcp/discover.
        self.tools: dict[str, str | StdioMcpServer] = dict(tools)
        retention_raw = _os.environ.get("AXOR_RETENTION_DAYS", "")
        self.runs = RunManager(
            trace_dir,
            retention_days=float(retention_raw) if retention_raw else None,
        )
        self.client = client or httpx.AsyncClient(timeout=30.0)
        self.uploader = uploader or (
            BackendUploader(backend_url, ingest_key=ingest_key) if backend_url else None
        )
        # Base URL the scripted agent (in-app experiment runner) dials to reach
        # this proxy's own tool routes; agent_client lets tests bind it to the
        # ASGI app in-process. Defaults to a real loopback client in production.
        self.self_base_url = self_base_url
        self.agent_client = agent_client
        # Where a spawned governed node uploads its trace and connects its
        # PlaneClient (heartbeats + desired-state). Kept so the governed-spawn
        # route can reach the backend directly, not only via the uploader.
        self.backend_url = backend_url
        self.ingest_key = ingest_key
        # Live governed nodes (node_id -> keepalive task) so they are not GC'd.
        self.governed: dict[str, Any] = {}
        # Vault mode (§14.2): the tools whose credentials this proxy injects,
        # and the client that fetches them. Both empty by default — §6
        # passthrough is what runs unless an operator opted a tool in, and a
        # proxy with no backend has nothing to fetch from.
        self.vault_tools: frozenset[str] = (
            vault_tool_names if vault_tool_names is not None else vault_tools()
        )
        self.vault = vault or (
            CredentialVault(backend_url, ingest_key=ingest_key)
            if backend_url and self.vault_tools else None
        )


async def _stdio_dispatch(
    state: ProxyState,
    run: Any,  # noqa: ANN401 - RunManager's run record
    tool: str,
    server: StdioMcpServer,
    body: bytes,
    spec: Any,  # noqa: ANN401 - fault spec or None
) -> Response:
    """Relay one JSON-RPC message to a stdio-MCP server. The TOOL_CALL event
    is already recorded by the caller; this handles fault/clean result paths.
    Buffered by nature — stdio MCP is one message per line, nothing streams."""
    try:
        parsed = json.loads(body) if body else None
    except json.JSONDecodeError:
        parsed = None
    if not isinstance(parsed, dict) or parsed.get("jsonrpc") != "2.0":
        return JSONResponse(
            {"error": "jsonrpc_required",
             "detail": "a stdio-MCP tool speaks JSON-RPC 2.0 — POST one "
                       "message object per request"},
            status_code=400,
        )

    async def upstream_or_502() -> tuple[dict[str, Any] | None, Response | None]:
        try:
            return await server.rpc(parsed), None
        except McpError as exc:
            run.call_counts[tool] = run.call_counts.get(tool, 0) + 1
            await state.runs.record(
                run, EventKind.TOOL_RESULT,
                {"tool": tool, "status": None, "error": "McpError",
                 "transport": "stdio"},
            )
            return None, JSONResponse(
                {"error": "upstream_unreachable", "tool": tool,
                 "detail": str(exc)},
                status_code=502,
            )

    # ── fault path: identical semantics to the HTTP fault path. ──
    if spec is not None:
        upstream_result: Any | None = None
        if state.runs.needs_upstream(spec):
            upstream_result, err = await upstream_or_502()
            if err is not None:
                return err
        faulted = state.runs.apply_fault(run, spec, upstream_result)
        fault = run.engine.fault_log[-1]
        await state.runs.record(
            run, EventKind.FAULT_INJECTED,
            {"tool": tool, "mode": fault.mode, "canary": fault.canary},
        )
        payload = json.dumps(faulted).encode()
        await state.runs.record(
            run, EventKind.TOOL_RESULT,
            {"tool": tool, "status": 200, "faulted": True,
             "response_bytes": len(payload),
             "response_sha256": sha256_hex(payload), "transport": "stdio"},
        )
        run.call_counts[tool] = run.call_counts.get(tool, 0) + 1
        return Response(payload, media_type="application/json")

    # ── clean path ──
    result, err = await upstream_or_502()
    if err is not None:
        return err
    run.call_counts[tool] = run.call_counts.get(tool, 0) + 1
    if result is None:
        # A notification: delivered, nothing to return (MCP stdio semantics).
        await state.runs.record(
            run, EventKind.TOOL_RESULT,
            {"tool": tool, "status": 202, "response_bytes": 0,
             "transport": "stdio"},
        )
        return Response(status_code=202)
    payload = json.dumps(result).encode()
    await state.runs.record(
        run, EventKind.TOOL_RESULT,
        {"tool": tool, "status": 200, "response_bytes": len(payload),
         "response_sha256": sha256_hex(payload), "transport": "stdio"},
    )
    return Response(payload, media_type="application/json")


def create_app(state: ProxyState) -> Starlette:
    def authorized(request: Request) -> bool:
        """Bearer check for the control surface. Open when no token is set."""
        if state.control_token is None:
            return True
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return False
        return secrets.compare_digest(header[7:].strip(), state.control_token)

    def unauthorized() -> Response:
        return JSONResponse(
            {"error": "unauthorized",
             "detail": "this proxy requires a bearer token on /axor routes "
                       "(AXOR_PROXY_TOKEN)"},
            status_code=401,
        )

    def guarded(handler: Any) -> Any:  # noqa: ANN401 - Starlette endpoint
        async def wrapped(request: Request) -> Response:
            if not authorized(request):
                return unauthorized()
            return await handler(request)

        wrapped.__name__ = handler.__name__
        return wrapped

    async def tool_route(request: Request) -> Response:
        tool = request.path_params["tool"]
        path = request.path_params.get("path", "")
        # Concurrent runs: X-Axor-Run names this caller's armed run; without it
        # the most recently armed run applies (single-user compat).
        run = state.runs.active_for(request.headers.get("x-axor-run"))
        if run is None:
            return JSONResponse(
                {"error": "proxy_disarmed",
                 "detail": "no armed run for this caller; start one via POST "
                           "/axor/runs (and send X-Axor-Run when sharing a proxy)"},
                status_code=503,
            )
        upstream_base = state.tools.get(tool)
        if upstream_base is None:
            return JSONResponse(
                {"error": "unknown_tool", "tool": tool}, status_code=404
            )

        body = await request.body()
        call_payload: dict[str, Any] = {
            "tool": tool,
            "path": path,
            "method": request.method,
            "request_bytes": len(body),
            "request_sha256": sha256_hex(body) if body else None,
        }
        # MCP granularity: a JSON-RPC tools/call body names the inner tool —
        # record `server:tool` instead of one opaque endpoint. Observation-only.
        rpc = sniff_rpc_call(body)
        governed_args: dict[str, Any] | None = None
        if rpc is not None:
            governed_args = rpc.get("arguments")
            # The arguments go to the governor, never to the trace: "no raw
            # bodies persisted" is the proxy's fourth rule and an argument map
            # IS the body.
            call_payload["rpc"] = {k: v for k, v in rpc.items() if k != "arguments"}
            if rpc.get("tool"):
                call_payload["tool"] = f"{tool}:{rpc['tool']}"
        # ── vault mode (§14.2), opt-in per tool: fetch the credential now and
        # inject it at the sink, so the agent never holds it. Fail closed —
        # every refusal ends the call here, before any upstream request. ──
        credential = None
        if tool in state.vault_tools:
            denial: str | None = None
            if state.vault is None:
                denial = (
                    f"{tool} is in AXOR_VAULT_TOOLS but this proxy has no "
                    "backend to dispense from"
                )
            elif isinstance(upstream_base, StdioMcpServer):
                # A stdio MCP server has no request headers to inject into.
                # Passing the call through unauthenticated would be the
                # fail-open the whole feature exists to remove.
                denial = (
                    f"{tool} is a stdio MCP tool: there is no header to inject "
                    "a credential into, and vault mode does not fall back to "
                    "passthrough"
                )
            else:
                try:
                    credential = await state.vault.dispense(
                        run.node_id, tool, upstream_base, run_id=run.run_id,
                    )
                except CredentialDenied as exc:
                    denial = exc.reason
            if denial is not None:
                # A refused tool call is a TOOL_CALL with `verdict: deny` —
                # axor-core's own event schema says so, and says why: "this
                # branch is the only one replay re-gates, and DENIAL is for
                # refusals that are not tool calls (a spawn, a message)". It
                # was written as an unlabelled TOOL_CALL *plus* a DENIAL, and
                # the replay fold has no DENIAL branch, so the refusal was
                # invisible to replay AND the unlabelled call charged the
                # budget for a request that never left the proxy.
                call_payload["credential"] = {"injected": False, "reason": denial}
                call_payload["reason"] = denial
                call_payload["category"] = "vault"
                await state.runs.record(
                    run, EventKind.TOOL_CALL, call_payload,
                    gate=_gate(VAULT_DENIAL_CATEGORY), verdict=Verdict.DENY,
                )
                run.call_counts[tool] = run.call_counts.get(tool, 0) + 1
                return JSONResponse(
                    {"error": "credential_denied", "tool": tool, "detail": denial},
                    status_code=403,
                )
            # Recorded, never the key material (§14.2): which tool, which
            # endpoint, which version — enough for replay to show "this call was
            # made WITH credential X injected" and nothing more.
            call_payload["credential"] = {
                "injected": True,
                "endpoint": upstream_base,
                "header": credential.header,
                "version": credential.version,
            }

        decision, gate, verdict = _govern(
            run, call_payload["tool"], governed_args, call_payload,
        )
        await state.runs.record(
            run, EventKind.TOOL_CALL, call_payload, gate=gate, verdict=verdict,
        )

        def outgoing() -> list[tuple[bytes, bytes]]:
            """Headers for the upstream call. Byte-for-byte passthrough, except
            that a vault-mode tool's injection header is REPLACED — the agent's
            own value there is not a fallback, it is what is being removed."""
            headers = _forward_headers(request.headers)
            return credential.applied_to(headers) if credential else headers

        spec = state.runs.fault_for_call(run, tool)

        # ── stdio-MCP gateway: the upstream is a local process, not a URL.
        # One JSON-RPC message in, one out — same fault/observation pipeline. ──
        if isinstance(upstream_base, StdioMcpServer):
            return await _stdio_dispatch(state, run, tool, upstream_base, body, spec)

        url = upstream_base.rstrip("/") + "/" + path if path else upstream_base

        # ── fault path: the deprivation engine transforms the JSON body, so it
        # has to be buffered. This is the test-bench path, never production. ──
        if spec is not None:
            upstream_result: Any | None = None
            if state.runs.needs_upstream(spec):
                try:
                    upstream = await state.client.request(
                        request.method, url,
                        params=request.query_params,
                        headers=outgoing(),
                        content=body,
                    )
                except httpx.HTTPError as exc:
                    run.call_counts[tool] = run.call_counts.get(tool, 0) + 1
                    await state.runs.record(
                        run, EventKind.TOOL_RESULT,
                        {"tool": tool, "status": None, "error": type(exc).__name__},
                    )
                    return JSONResponse(
                        {"error": "upstream_unreachable", "tool": tool,
                         "detail": str(exc)},
                        status_code=502,
                    )
                try:
                    upstream_result = upstream.json()
                except (json.JSONDecodeError, ValueError):
                    upstream_result = {"content": upstream.text}

            faulted = state.runs.apply_fault(run, spec, upstream_result)
            fault = run.engine.fault_log[-1]
            await state.runs.record(
                run,
                EventKind.FAULT_INJECTED,
                {"tool": tool, "mode": fault.mode, "canary": fault.canary},
            )
            payload = json.dumps(faulted).encode()
            await state.runs.record(
                run,
                EventKind.TOOL_RESULT,
                {"tool": tool, "status": 200, "faulted": True,
                 "response_bytes": len(payload),
                 "response_sha256": sha256_hex(payload),
                 **_ledger(_register(run, decision, faulted))},
            )
            run.call_counts[tool] = run.call_counts.get(tool, 0) + 1
            return Response(payload, media_type="application/json")

        # ── clean passthrough: STREAM the upstream body through (launch
        # readiness §1) — an LLM-backed tool answering over SSE/chunked must
        # flow, not stall behind a full-body buffer. The observation (size +
        # sha256) accumulates over the stream and is recorded when it ends. ──
        req = state.client.build_request(
            request.method, url,
            params=request.query_params,
            headers=outgoing(),
            content=body,
        )
        try:
            upstream = await state.client.send(req, stream=True)
        except httpx.HTTPError as exc:
            run.call_counts[tool] = run.call_counts.get(tool, 0) + 1
            await state.runs.record(
                run, EventKind.TOOL_RESULT,
                {"tool": tool, "status": None, "error": type(exc).__name__},
            )
            return JSONResponse(
                {"error": "upstream_unreachable", "tool": tool, "detail": str(exc)},
                status_code=502,
            )
        resp_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }
        run.call_counts[tool] = run.call_counts.get(tool, 0) + 1

        # ── governed run, non-streaming answer: read the body so the ledger
        # sees the value. A per-value taint gate is only as good as what the
        # ledger holds, and a ledger that never saw an upstream result lets the
        # next egress call pass on nothing. Buffering is confined to a run the
        # operator armed WITH manifests — an ungoverned run still streams, and
        # so does an event-stream, which must flow whatever the run is. ──
        if run.governor is not None and decision is not None and not _is_stream(
            upstream.headers.get("content-type", "")
        ):
            raw = await upstream.aread()
            await upstream.aclose()
            try:
                value: Any = json.loads(raw) if raw else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                value = raw.decode("utf-8", "replace")
            await state.runs.record(run, EventKind.TOOL_RESULT, {
                "tool": tool, "status": upstream.status_code,
                "response_bytes": len(raw),
                "response_sha256": hasher_hex(raw),
                **_ledger(_register(run, decision, _rpc_value(value))),
            })
            return Response(raw, status_code=upstream.status_code,
                            headers=resp_headers)

        async def relay() -> Any:  # noqa: ANN401 - async byte generator
            hasher = hashlib.sha256()
            count = 0
            error: str | None = None
            try:
                # aiter_raw: bytes exactly as received (content-encoding intact,
                # matching the forwarded headers).
                async for chunk in upstream.aiter_raw():
                    hasher.update(chunk)
                    count += len(chunk)
                    yield chunk
            except httpx.HTTPError as exc:
                error = type(exc).__name__
                raise
            finally:
                await upstream.aclose()
                result_payload: dict[str, Any] = {
                    "tool": tool, "status": upstream.status_code,
                    "response_bytes": count,
                    "response_sha256": hasher.hexdigest(),
                    "streamed": True,
                }
                if error is not None:
                    result_payload["error"] = error
                if run.governor is not None:
                    # Streamed: the body passes through and is never buffered
                    # (rule 4, and an SSE-backed tool must flow). The ledger
                    # therefore did not see this value, and a later call that
                    # carries it cannot resolve to a ref. Recorded, not implied.
                    result_payload["ledger"] = {
                        "registered": False,
                        "reason": "streamed: the body is relayed, never buffered",
                    }
                await state.runs.record(run, EventKind.TOOL_RESULT, result_payload)

        return StreamingResponse(
            relay(), status_code=upstream.status_code, headers=resp_headers,
        )

    async def start_run(request: Request) -> Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "malformed_json"}, status_code=400)
        manifests = payload.get("manifests")
        if manifests is not None and (
            not isinstance(manifests, list)
            or not all(isinstance(m, dict) for m in manifests)
        ):
            return JSONResponse(
                {"error": "bad_manifests",
                 "detail": "manifests must be a list of tool-manifest/v1 objects "
                           "(what the config builder emits)"},
                status_code=400,
            )
        try:
            run = state.runs.start(
                scenario=payload.get("scenario", "custom"),
                faults=payload.get("faults", []),
                node_id=payload.get("node_id", "proxy"),
                manifests=manifests,
            )
        except Exception as exc:  # noqa: BLE001 — the operator's manifests
            # Arming with manifests the governor cannot be built from is a 400
            # naming the reason, not a run that silently records no verdicts.
            return JSONResponse(
                {"error": "bad_manifests", "detail": f"{type(exc).__name__}: {exc}"},
                status_code=400,
            )
        return JSONResponse(
            {"run_id": run.run_id, "armed": True,
             "governed": run.governor is not None,
             "tools": {t: f"/t/{t}/" for t in state.tools}},
            status_code=201,
        )

    async def simulate(request: Request) -> Response:
        """Run our scripted agent against an armed run — the in-app experiment
        loop (demo-mode / try-it). Drives real tool calls + a claim through the
        same pipeline an external agent would, then returns the receipt."""
        run = state.runs.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown_run"}, status_code=404)
        if not state.runs.is_armed(run):
            return JSONResponse(
                {"error": "run_not_armed",
                 "detail": "simulate needs the run armed; it disarms on claim"},
                status_code=409,
            )
        payload = await request.json() if await request.body() else {}
        faults = [{"tool": s.tool, "mode": s.mode} for s in run.faults]
        script = payload.get("script") or default_script(faults, list(state.tools))
        claim = payload.get("claim") or default_claim(faults)

        owns = state.agent_client is None
        client = state.agent_client or httpx.AsyncClient(timeout=30.0)
        try:
            agent = ScriptedAgent(state.self_base_url, client)
            result = await agent.run(run.run_id, script, claim)
        finally:
            if owns:
                await client.aclose()
        return JSONResponse(result)

    async def submit_claim(request: Request) -> Response:
        run = state.runs.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown_run"}, status_code=404)
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "malformed_json"}, status_code=400)
        cases = await state.runs.submit_claim(
            run, payload.get("text", ""), payload.get("claims")
        )
        upload: dict[str, Any] | None = None
        if state.uploader is not None:
            upload = await state.uploader.upload(run, run.recorder.path)
        return JSONResponse({
            "run_id": run.run_id,
            "evidence": [evidence_to_dict(c) for c in cases],
            "deviations": sum(1 for c in cases if c.deviation is not None),
            "upload": upload,
        })

    async def get_run(request: Request) -> Response:
        run = state.runs.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown_run"}, status_code=404)
        return JSONResponse({
            "run_id": run.run_id,
            "scenario": run.scenario,
            "completed": run.completed,
            "armed": state.runs.active is run,
            "call_counts": run.call_counts,
            "trace_path": str(run.recorder.path),
            "evidence": [evidence_to_dict(c) for c in run.evidence],
        })

    async def preflight(request: Request) -> Response:
        """Onboarding step 3: prove the plumbing before it matters."""
        results: dict[str, dict[str, Any]] = {}
        for tool, base in state.tools.items():
            if isinstance(base, StdioMcpServer):
                results[tool] = {"ok": base.alive, "transport": "stdio"}
                continue
            try:
                resp = await state.client.request("GET", base, timeout=5.0)
                results[tool] = {"ok": resp.status_code < 500,
                                 "status": resp.status_code}
            except httpx.HTTPError as exc:
                results[tool] = {"ok": False, "error": type(exc).__name__}
        return JSONResponse({
            "all_ok": all(r["ok"] for r in results.values()) if results else False,
            "tools": results,
        })

    async def spawn_governed_tree_route(request: Request) -> Response:
        """Spawn the REAL governed tree (spec v2 Ch.4): three IntentLoop nodes
        over the axor-core message bus — authentic per-node verdicts, labels
        carried in envelopes, export denied at the orchestrator. Uploads the
        multi-node trace + the CONTAINED case. Requires --backend-url."""
        if state.backend_url is None:
            return JSONResponse(
                {"error": "no_backend",
                 "detail": "governed tree needs the proxy started with --backend-url"},
                status_code=409,
            )
        from axor_proxy.governed import spawn_governed_tree

        try:
            result = await spawn_governed_tree(state.backend_url, state.ingest_key)
        except httpx.HTTPError as exc:
            return JSONResponse(
                {"error": "backend_upload_failed", "detail": str(exc)},
                status_code=502,
            )
        return JSONResponse(result)

    async def spawn_governed(request: Request) -> Response:
        """Spawn a REAL governed node (axor-core IntentLoop): it runs a governed
        session (recorded taint denial), uploads the adapter-fidelity trace, and
        stays live on the plane (heartbeats + desired-state) so Control shows it
        and interventions reach it. Requires --backend-url."""
        if state.backend_url is None:
            return JSONResponse(
                {"error": "no_backend",
                 "detail": "governed spawn needs the proxy started with --backend-url"},
                status_code=409,
            )
        from axor_proxy.governed import spawn_governed_node

        try:
            result = await spawn_governed_node(state.backend_url, state.ingest_key)
        except httpx.HTTPError as exc:
            # The trace upload failed — say so instead of reporting a live node.
            return JSONResponse(
                {"error": "backend_upload_failed", "detail": str(exc)},
                status_code=502,
            )
        task = result.pop("_task")
        node_id = result["node_id"]
        state.governed[node_id] = task
        # The keepalive ends after its TTL — drop the handle so repeated spawns
        # don't accumulate finished tasks.
        task.add_done_callback(lambda _t: state.governed.pop(node_id, None))
        return JSONResponse(result)

    async def mcp_discover(request: Request) -> Response:
        """Onboarding: point us at an MCP server → we handshake, list its
        tools, and register the server as a proxied tool endpoint, so
        /t/{name}/ fronts it immediately (faults, observation — like any
        other tool). Two transports: {url} for streamable-HTTP servers,
        {command} for local stdio servers (the gateway spawns the process)."""
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "malformed_json"}, status_code=400)
        url = payload.get("url")
        command = payload.get("command")

        if isinstance(command, str) and command.strip():
            import shlex

            command = shlex.split(command)
        stdio_ok = (
            isinstance(command, list) and command
            and all(isinstance(c, str) and c for c in command)
        )
        if not stdio_ok and (not isinstance(url, str) or not url):
            return JSONResponse(
                {"error": "url_or_command_required",
                 "detail": "POST {url, name?} for an HTTP MCP server, or "
                           "{command: [\"npx\", …], name?} for a local stdio "
                           "server (the proxy spawns it as a gateway)."},
                status_code=400,
            )

        if stdio_ok:
            # Spawning a process is a step up from dialing a URL: only accept
            # stdio registrations from loopback callers (the operator on the
            # same box), unless explicitly opened up. Keeps an exposed proxy
            # port from being a remote-exec endpoint.
            import ipaddress
            import os

            client_host = request.client.host if request.client else ""
            try:
                is_local = ipaddress.ip_address(client_host).is_loopback
            except ValueError:
                is_local = False
            if not is_local and os.environ.get("AXOR_ALLOW_REMOTE_STDIO") != "1":
                return JSONResponse(
                    {"error": "stdio_requires_loopback",
                     "detail": "stdio-MCP registration spawns a local process; "
                               "only loopback callers may do that (set "
                               "AXOR_ALLOW_REMOTE_STDIO=1 to override)."},
                    status_code=403,
                )
            try:
                server = await discover_stdio(command)
            except McpError as exc:
                return JSONResponse(
                    {"error": "mcp_discovery_failed", "detail": str(exc)},
                    status_code=502,
                )
            name = payload.get("name") or server.server_name
            # Repointing an existing stdio registration must not leak the old
            # process.
            old = state.tools.get(name)
            if isinstance(old, StdioMcpServer):
                await old.close()
            state.tools[name] = server
            return JSONResponse({
                "registered": name,
                "proxied_base": f"/t/{name}/",
                "server": server.server_name,
                "protocol_version": server.protocol_version,
                "transport": "stdio",
                "tools": server.tools,
            })

        try:
            info = await discover(url, state.client)
        except McpError as exc:
            return JSONResponse(
                {"error": "mcp_discovery_failed", "detail": str(exc)},
                status_code=502,
            )
        name = payload.get("name") or info["server"]
        # Registering under an existing name repoints it — explicit, visible in
        # the response; the tool table is dev-scoped runtime state.
        old = state.tools.get(name)
        if isinstance(old, StdioMcpServer):
            await old.close()
        state.tools[name] = url
        return JSONResponse({
            "registered": name,
            "proxied_base": f"/t/{name}/",
            "server": info["server"],
            "protocol_version": info["protocol_version"],
            "transport": "http",
            "tools": info["tools"],
        })

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True, "armed": state.runs.active is not None})

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> Any:  # noqa: ANN401 - CM protocol
        yield
        # Spawned stdio-MCP gateways die with the proxy, not as orphans.
        for upstream in state.tools.values():
            if isinstance(upstream, StdioMcpServer):
                await upstream.close()

    app = Starlette(lifespan=lifespan, routes=[
        # healthz stays open: it is what a container's health check calls, and
        # it reveals only liveness plus whether a run is armed.
        Route("/axor/healthz", healthz),
        Route("/axor/preflight", guarded(preflight)),
        Route("/axor/mcp/discover", guarded(mcp_discover), methods=["POST"]),
        Route("/axor/governed/spawn", guarded(spawn_governed), methods=["POST"]),
        Route("/axor/governed/spawn-tree", guarded(spawn_governed_tree_route),
              methods=["POST"]),
        Route("/axor/runs", guarded(start_run), methods=["POST"]),
        Route("/axor/runs/{run_id}/simulate", guarded(simulate), methods=["POST"]),
        Route("/axor/runs/{run_id}/claim", guarded(submit_claim), methods=["POST"]),
        Route("/axor/runs/{run_id}", guarded(get_run)),
        Mount("/mock", app=mock_tools_app()),
        Route(
            "/t/{tool}/{path:path}", tool_route,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
        ),
    ])
    app.state.proxy = state
    return app
