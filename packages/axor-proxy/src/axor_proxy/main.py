"""Entry point: uvx axor-proxy --config tools.json

tools.json:
    {"tools": {"web_search": "https://api.search.example/v1",
               "send_report": "https://slack.example/api/post"}}

Demo-mode (zero config): `axor-proxy --demo` registers the built-in mock tools
(web_search + generic MCP) served by the proxy itself.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def cli() -> None:
    parser = argparse.ArgumentParser(prog="axor-proxy")
    parser.add_argument("--config", type=Path, help="tools.json")
    parser.add_argument("--demo", action="store_true",
                        default=os.environ.get("AXOR_PROXY_DEMO", "") == "1",
                        help="serve built-in mock tools (no config needed)")
    parser.add_argument("--host", default=os.environ.get("AXOR_PROXY_HOST", "127.0.0.1"),
                        help="bind address")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("AXOR_PROXY_PORT", "8401")))
    parser.add_argument("--trace-dir", type=Path,
                        default=Path(os.environ.get("AXOR_TRACE_DIR", "./axor-traces")))
    parser.add_argument("--backend-url",
                        default=os.environ.get("AXOR_BACKEND_URL"),
                        help="push trace + evidence to this backend on claim")
    args = parser.parse_args()

    # The self-dial host (scripted agent + demo mock upstream) must be a routable
    # loopback, not the wildcard bind address: a container binds 0.0.0.0 but must
    # dial 127.0.0.1 to reach its own routes.
    self_host = "127.0.0.1" if args.host in ("0.0.0.0", "::", "") else args.host

    tools: dict[str, str] = {}
    if args.config:
        tools.update(json.loads(args.config.read_text())["tools"])
    if args.demo or not tools:
        base = f"http://{self_host}:{args.port}/mock"
        tools.setdefault("web_search", f"{base}/web_search")
        tools.setdefault("mcp", f"{base}/mcp")

    import uvicorn

    from axor_proxy.app import ProxyState, create_app

    args.trace_dir.mkdir(parents=True, exist_ok=True)
    self_base = f"http://{self_host}:{args.port}"
    app = create_app(ProxyState(tools=tools, trace_dir=args.trace_dir,
                                backend_url=args.backend_url,
                                self_base_url=self_base,
                                ingest_key=os.environ.get("AXOR_INGEST_KEY")))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    cli()
