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
from pathlib import Path


def cli() -> None:
    parser = argparse.ArgumentParser(prog="axor-proxy")
    parser.add_argument("--config", type=Path, help="tools.json")
    parser.add_argument("--demo", action="store_true",
                        help="serve built-in mock tools (no config needed)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8401)
    parser.add_argument("--trace-dir", type=Path, default=Path("./axor-traces"))
    args = parser.parse_args()

    tools: dict[str, str] = {}
    if args.config:
        tools.update(json.loads(args.config.read_text())["tools"])
    if args.demo or not tools:
        base = f"http://{args.host}:{args.port}/mock"
        tools.setdefault("web_search", f"{base}/web_search")
        tools.setdefault("mcp", f"{base}/mcp")

    import uvicorn

    from axor_proxy.app import ProxyState, create_app

    args.trace_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(ProxyState(tools=tools, trace_dir=args.trace_dir))
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    cli()
