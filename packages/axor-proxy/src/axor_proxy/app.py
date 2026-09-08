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
- Observe-only: the proxy never blocks the agent.
- No raw bodies persisted: observations carry status, sizes and hashes only.
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
from axor_core.kernel.events import EventKind
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route

from axor_proxy.agent import ScriptedAgent, default_claim, default_script
from axor_proxy.mcp import McpError, discover, sniff_rpc_call
from axor_proxy.mock_tools import mock_tools_app
from axor_proxy.runs import RunManager, evidence_to_dict, sha256_hex
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
        if rpc is not None:
            call_payload["rpc"] = rpc
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
                call_payload["credential"] = {"injected": False, "reason": denial}
                await state.runs.record(run, EventKind.TOOL_CALL, call_payload)
                await state.runs.record(run, EventKind.DENIAL, {
                    "tool": tool, "category": "vault", "reason": denial,
                    "intent_kind": "tool_call",
                })
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

        await state.runs.record(run, EventKind.TOOL_CALL, call_payload)

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
                 "response_sha256": sha256_hex(payload)},
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
                await state.runs.record(run, EventKind.TOOL_RESULT, result_payload)

        return StreamingResponse(
            relay(), status_code=upstream.status_code, headers=resp_headers,
        )

    async def start_run(request: Request) -> Response:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "malformed_json"}, status_code=400)
        run = state.runs.start(
            scenario=payload.get("scenario", "custom"),
            faults=payload.get("faults", []),
            node_id=payload.get("node_id", "proxy"),
        )
        return JSONResponse(
            {"run_id": run.run_id, "armed": True,
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
