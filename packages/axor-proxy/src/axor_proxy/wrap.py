"""`axor-proxy wrap -- <agent command>` — the no-code-change path for CLI agents.

The observe-only proxy sees your agent's tool calls but not the one thing that
makes an EvidenceCase: the agent's own final answer (the *claim*). For an agent
whose source you edit, `examples/axor_hook.py` submits that claim. For an agent
you run as a subprocess — a CLI, a script, anything that prints its answer to
stdout — `wrap` submits it for you, without touching the agent:

    axor-proxy --demo &                                  # a proxy is running
    axor-proxy wrap --fault web_search:silent_fail -- my-agent "what did rates do?"

`wrap` arms a run on the proxy, runs the command (streaming its output through
untouched), captures stdout, and submits it as the claim — then prints whether
a discrepancy was caught. The child is told the run via `AXOR_RUN` /
`AXOR_PROXY_URL` in its environment, so a cooperating tool binds to the right
run; on a single-run proxy the tool traffic binds automatically.

Use `wrap` *or* the in-code hook, not both — either one submits the claim.

Detection strength: `wrap` can only submit a *text-only* claim (the stdout),
and free-text detection is narrow by design — it keys on a tool's name appearing
near a success verb in the answer, to keep false positives near zero. A natural
answer that never names its tools may not trip it, so a clean `wrap` result is
not a guarantee. The deterministic path (confidence 1.0) is the in-code hook,
which reports *structured* tool outcomes; reach for it when you need certainty.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import TextIO

import httpx

DEFAULT_PROXY_URL = os.environ.get(
    "AXOR_PROXY_URL", f"http://127.0.0.1:{os.environ.get('AXOR_PROXY_PORT', '8401')}"
)

# Fault modes the axor-eval engine applies. Validated here so a typo fails fast
# with a clear message instead of a 500 from the engine (repo honesty rule).
VALID_FAULT_MODES = frozenset(
    {"silent_fail", "corrupt_retrieval", "instruction_injection", "tool_substitution"}
)
DEFAULT_FAULT_MODE = "silent_fail"


def _parse_faults(specs: list[str]) -> list[dict[str, str]]:
    """`tool:mode` → {"tool": tool, "mode": mode}. Bare `tool` ⇒ `silent_fail`."""
    faults: list[dict[str, str]] = []
    for spec in specs:
        tool, _, mode = spec.partition(":")
        mode = mode or DEFAULT_FAULT_MODE
        if not tool:
            raise ValueError(f"bad --fault {spec!r}: expected tool[:mode]")
        if mode not in VALID_FAULT_MODES:
            raise ValueError(
                f"unknown fault mode {mode!r} in --fault {spec!r}; "
                f"valid: {', '.join(sorted(VALID_FAULT_MODES))}"
            )
        faults.append({"tool": tool, "mode": mode})
    return faults


def arm_run(client: httpx.Client, scenario: str, faults: list[dict[str, str]]) -> str:
    """Arm a run on the proxy and return its id."""
    r = client.post("/axor/runs", json={"scenario": scenario, "faults": faults})
    r.raise_for_status()
    return str(r.json()["run_id"])


def submit_claim(client: httpx.Client, run_id: str, text: str) -> dict[str, object]:
    """Submit the captured final answer as a text-only claim; return the receipt."""
    r = client.post(f"/axor/runs/{run_id}/claim", json={"text": text})
    r.raise_for_status()
    return dict(r.json())


def tee(command: list[str], env: dict[str, str], out: TextIO | None = None) -> tuple[int, str]:
    """Run `command`, streaming its stdout to `out` while capturing it verbatim.

    stdin and stderr inherit, so prompts and logs (stderr by convention) still
    reach the terminal untouched; only stdout — the agent's answer — is
    captured. `out` resolves at call time (defaults to the live `sys.stdout`).
    Returns (exit_code, captured_stdout)."""
    stream = out if out is not None else sys.stdout
    proc = subprocess.Popen(  # noqa: S603 - command is the user's own agent invocation
        command, stdout=subprocess.PIPE, env=env, text=True, bufsize=1
    )
    captured: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        stream.write(line)
        stream.flush()
        captured.append(line)
    proc.stdout.close()
    return proc.wait(), "".join(captured)


def wrap_cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="axor-proxy wrap",
        description="Run a CLI agent behind a running Axor proxy and submit its "
                    "stdout as the claim, so a caught discrepancy becomes an EvidenceCase.",
    )
    parser.add_argument("--proxy-url", default=DEFAULT_PROXY_URL,
                        help=f"base URL of the running proxy (default {DEFAULT_PROXY_URL})")
    parser.add_argument("--scenario", default="custom", help="scenario label for the run")
    _modes = ", ".join(sorted(VALID_FAULT_MODES))
    parser.add_argument("--fault", action="append", default=[], metavar="TOOL[:MODE]",
                        help=f"arm a fault on a tool (repeatable); MODE defaults to "
                             f"'{DEFAULT_FAULT_MODE}', one of {_modes}. "
                             f"Omit for an observe-only run.")
    parser.add_argument("--run-id", default=None,
                        help="reuse an already-armed run instead of arming a new one")
    parser.add_argument("command", nargs=argparse.REMAINDER,
                        help="the agent command, after `--` (e.g. -- my-agent 'question')")
    args = parser.parse_args(argv)

    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        parser.error("no agent command given; put it after `--`")

    try:
        faults = _parse_faults(args.fault)
    except ValueError as exc:
        parser.error(str(exc))

    client = httpx.Client(base_url=args.proxy_url.rstrip("/"), timeout=30.0)
    try:
        try:
            run_id = args.run_id or arm_run(client, args.scenario, faults)
        except httpx.HTTPError as exc:
            print(f"[axor] could not reach the proxy at {args.proxy_url} ({exc}); "
                  f"is `axor-proxy` running?", file=sys.stderr)
            return 2
        print(f"[axor] run {run_id} armed ({len(faults)} fault(s)); running agent…",
              file=sys.stderr)

        env = {**os.environ, "AXOR_RUN": run_id, "AXOR_PROXY_URL": args.proxy_url.rstrip("/")}
        code, output = tee(command, env)

        try:
            receipt = submit_claim(client, run_id, output)
        except httpx.HTTPError as exc:
            print(f"[axor] claim not submitted ({exc}); agent exit={code}", file=sys.stderr)
            return code
    finally:
        client.close()

    deviations = int(receipt.get("deviations", 0) or 0)
    evidence = receipt.get("evidence") or []
    if deviations:
        sources = {e.get("verdict_source") for e in evidence if isinstance(e, dict)}
        how = "heuristic" if sources == {"heuristic"} else "deterministic"
        print(f"\n[axor] ⚠ caught {deviations} discrepancy(ies) ({how}) — "
              f"{len(evidence)} EvidenceCase(s) for run {run_id}", file=sys.stderr)
    else:
        # wrap can only submit a text-only claim, and free-text detection is
        # narrow by design (it keys on the tool's name appearing near a success
        # verb in the answer). "No discrepancy" here is therefore not a clean
        # bill of health — say so, and point at the deterministic path.
        print(f"\n[axor] no discrepancy found for run {run_id} — but this was a "
              f"text-only claim (heuristic, narrow). For deterministic detection, "
              f"have the agent report structured tool outcomes via the in-code hook "
              f"(examples/axor_hook.py).", file=sys.stderr)
    return code
