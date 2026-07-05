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

import json
from pathlib import Path
from typing import Any

import httpx
from axor_core.kernel.events import EventKind
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route

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
    ) -> None:
        self.tools = tools  # tool name -> upstream base url
        self.runs = RunManager(trace_dir)
        self.client = client or httpx.AsyncClient(timeout=30.0)
        self.uploader = uploader or (
            BackendUploader(backend_url) if backend_url else None
        )


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
        await state.runs.record(
            run,
            EventKind.TOOL_CALL,
            {
                "tool": tool,
                "path": path,
                "method": request.method,
                "request_bytes": len(body),
                "request_sha256": sha256_hex(body) if body else None,
            },
        )

        spec = state.runs.fault_for_call(run, tool)
        upstream_result: Any | None = None
        upstream_status: int | None = None
        response: Response

        if spec is None or state.runs.needs_upstream(spec):
            url = upstream_base.rstrip("/") + "/" + path if path else upstream_base
            try:
                upstream = await state.client.request(
                    request.method,
                    url,
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
            upstream_status = upstream.status_code
            if spec is not None:
                try:
                    upstream_result = upstream.json()
                except (json.JSONDecodeError, ValueError):
                    upstream_result = {"content": upstream.text}

        if spec is not None:
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
            response = Response(payload, media_type="application/json")
        else:
            resp_headers = {
                k: v for k, v in upstream.headers.items()
                if k.lower() not in _HOP_BY_HOP
            }
            await state.runs.record(
                run,
                EventKind.TOOL_RESULT,
                {"tool": tool, "status": upstream_status,
                 "response_bytes": len(upstream.content),
                 "response_sha256": sha256_hex(upstream.content)},
            )
            response = Response(
                upstream.content,
                status_code=upstream.status_code,
                headers=resp_headers,
            )

        run.call_counts[tool] = run.call_counts.get(tool, 0) + 1
        return response

    async def start_run(request: Request) -> Response:
        payload = await request.json()
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

    async def submit_claim(request: Request) -> Response:
        run = state.runs.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "unknown_run"}, status_code=404)
        payload = await request.json()
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

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True, "armed": state.runs.active is not None})

    app = Starlette(routes=[
        Route("/axor/healthz", healthz),
        Route("/axor/preflight", preflight),
        Route("/axor/runs", start_run, methods=["POST"]),
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
