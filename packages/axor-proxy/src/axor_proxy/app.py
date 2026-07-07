"""The observe-only passthrough proxy (spec section 6).

Rules, in order of importance:
- Auth is passthrough, byte-for-byte: the Authorization header (and everything
  else except hop-by-hop headers) is forwarded untouched, never parsed, never
  stored.
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
from axor_proxy.upload import BackendUploader

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
    ) -> None:
        self.tools = tools  # tool name -> upstream base url
        self.runs = RunManager(trace_dir)
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


def create_app(state: ProxyState) -> Starlette:
    async def tool_route(request: Request) -> Response:
        tool = request.path_params["tool"]
        path = request.path_params.get("path", "")
        run = state.runs.active
        if run is None:
            return JSONResponse(
                {"error": "proxy_disarmed",
                 "detail": "no armed run; start one via POST /axor/runs"},
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
        await state.runs.record(run, EventKind.TOOL_CALL, call_payload)

        spec = state.runs.fault_for_call(run, tool)
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
                        headers=_forward_headers(request.headers),
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
            headers=_forward_headers(request.headers),
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
        if state.runs.active is not run:
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
        """Onboarding: point us at an HTTP MCP server → we handshake, list its
        tools, and register the server as a proxied tool endpoint, so
        /t/{name}/ fronts it immediately (auth passthrough, faults,
        observation — like any other tool). stdio servers are out of scope
        here and rejected honestly."""
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "malformed_json"}, status_code=400)
        url = payload.get("url")
        if not isinstance(url, str) or not url:
            return JSONResponse(
                {"error": "url_required",
                 "detail": "POST {url, name?} — url of an HTTP MCP server. "
                           "stdio servers need a local gateway (roadmap)."},
                status_code=400,
            )
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
        state.tools[name] = url
        return JSONResponse({
            "registered": name,
            "proxied_base": f"/t/{name}/",
            "server": info["server"],
            "protocol_version": info["protocol_version"],
            "tools": info["tools"],
        })

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True, "armed": state.runs.active is not None})

    app = Starlette(routes=[
        Route("/axor/healthz", healthz),
        Route("/axor/preflight", preflight),
        Route("/axor/mcp/discover", mcp_discover, methods=["POST"]),
        Route("/axor/governed/spawn", spawn_governed, methods=["POST"]),
        Route("/axor/runs", start_run, methods=["POST"]),
        Route("/axor/runs/{run_id}/simulate", simulate, methods=["POST"]),
        Route("/axor/runs/{run_id}/claim", submit_claim, methods=["POST"]),
        Route("/axor/runs/{run_id}", get_run),
        Mount("/mock", app=mock_tools_app()),
        Route(
            "/t/{tool}/{path:path}", tool_route,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
        ),
    ])
    app.state.proxy = state
    return app
