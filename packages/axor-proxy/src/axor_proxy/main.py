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
import sys
from pathlib import Path


def cli() -> None:
    # `axor-proxy run …` is a subcommand (run a CLI agent + submit its claim);
    # everything else is the server, whose flat flag parser is kept unchanged.
    #
    # It was `wrap`, which is the one word this repo cannot spare: axor-wrap's
    # `WrappedToolset` wraps tool callables and GOVERNS them — gate, taint,
    # verdict. This subcommand gates nothing; it runs a subprocess and submits
    # its answer as the claim. The old spelling still works, because silently
    # breaking a documented command is worse than an awkward name, and it says
    # what it is now called.
    if sys.argv[1:2] in (["run"], ["wrap"]):
        if sys.argv[1] == "wrap":
            print("[axor] `axor-proxy wrap` is now `axor-proxy run` — it submits "
                  "a CLI agent's answer as the claim and governs nothing; "
                  "`wrap` belongs to axor-wrap's WrappedToolset.", file=sys.stderr)
        from axor_proxy.run_cli import run_cli
        raise SystemExit(run_cli(sys.argv[2:]))
    # `axor-proxy vault …` — the two operations envelope mode needs and that
    # nothing else can do for you: mint the deployment's sealing keypair, and
    # seal a credential to it before enrolling. Both are local; the private half
    # never leaves this machine, which is the whole property.
    if sys.argv[1:2] == ["vault"]:
        raise SystemExit(_vault_cli(sys.argv[2:]))

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
    parser.add_argument("--token", default=os.environ.get("AXOR_PROXY_TOKEN"),
                        help="require this bearer token on the /axor control "
                             "routes (arm runs, inject faults, read traces). "
                             "Unset = open, which is fine on loopback and not "
                             "on a published port.")
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
    state = ProxyState(tools=tools, trace_dir=args.trace_dir,
                       backend_url=args.backend_url,
                       self_base_url=self_base,
                       ingest_key=os.environ.get("AXOR_INGEST_KEY"),
                       control_token=args.token)
    if state.control_token is None and args.host not in ("127.0.0.1", "localhost", "::1"):
        # Binding beyond loopback with no token means anyone who can reach the
        # port can arm runs, inject faults into live tool traffic and read back
        # every recorded trace. Say so, in the same voice the backend uses for
        # its own open posture.
        import logging

        logging.getLogger("axor.proxy").warning(
            "PROXY CONTROL SURFACE IS OPEN (no AXOR_PROXY_TOKEN) and bound to "
            "%s — anyone who reaches this port can arm runs, inject faults and "
            "read traces. Set a token for any non-loopback bind.", args.host,
        )
    app = create_app(state)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    cli()


def _vault_cli(argv: list[str]) -> int:
    from axor_proxy.vault import generate_sealing_key, seal

    parser = argparse.ArgumentParser(prog="axor-proxy vault")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="mint a credential-sealing keypair")
    seal_cmd = sub.add_parser(
        "seal", help="seal a credential to the deployment's sealing key")
    seal_cmd.add_argument("--public-key", required=True,
                          help="the deployment's sealing pubkey (hex)")
    seal_cmd.add_argument("--secret", help="the credential; omit to read stdin")
    args = parser.parse_args(argv)

    if args.cmd == "keygen":
        seed, public = generate_sealing_key()
        # The seed goes to the nodes, the pubkey to the plane. Printed as the
        # two env/route names they belong to, so the halves do not get swapped.
        print(f"AXOR_CRED_SEALING_SEED={seed}")
        print(f"public_key_hex={public}   "
              f"# POST /v1/vault/creds/sealing-key")
        return 0

    secret = args.secret if args.secret is not None else sys.stdin.read().strip()
    if not secret:
        print("nothing to seal", file=sys.stderr)
        return 2
    print(seal(args.public_key, secret))
    return 0
