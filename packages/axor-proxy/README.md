# axor-proxy

Tool proxy (Starlette + httpx), observe-only unless started in guard mode
(below). Sits in front of the agent's tools;
forwards auth byte-for-byte, and only ever does two things: inject a declared
fault (armed scenario), record an observation (status/sizes/hashes — never raw
bodies). It never blocks the agent.

Arm a run with `manifests` (the tool-manifest/v1 objects the Config Builder
emits) and it also holds an `axor_core.governor.ToolCallGovernor` for that run:
every call whose arguments it can see is evaluated and the verdict recorded, the
per-value taint ledger carries across the run, and nothing is blocked — the
posture axor-wrap spells `enforcement="off"`, with the same governor. This is
the one integration that is not that flag on a `WrappedToolset`: an HTTP
boundary has no callable to wrap. Without manifests nothing is governed, and the
trace keeps the shape it had — a tool's effect class is the operator's
declaration, never guessed from its name.

## Guard mode — block, not only watch

```
axor-proxy --guard --config tools.json --manifests axor.manifests.json --node-id support-agent
```

Every call that reaches `/t/{tool}` without an armed test run is decided
first, by the same `ToolCallGovernor` a `WrappedToolset` runs, built from the
same manifests (the Config Builder's `axor.manifests.json`, or a directory from
`axor-wrap manifest`; a manifest's `id` is the proxy's tool name, or an MCP
tool's name). Denied by policy → 403 (MCP `tools/call` → a JSON-RPC error);
paused / stopped from the Control Plane → 503; over the plane's call budget →
429. The upstream is never called for a refused call. An answer's text is
registered in the session's per-value ledger — a JSON answer as its string
leaves, so a value lifted out of it (an address, a command) is recognised when
it drives a later call. `X-Axor-Session` keeps one conversation's untrusted
text out of another's calls (default: one session, ended after 15 idle
minutes). With `AXOR_PLANE_URL` (or `--backend-url`) and `AXOR_INGEST_KEY` the
guard is a node on the plane: pause / stop / budget reach it and its calls show
in Agents — arguments as names and sizes, never values. What it cannot do:
anything on the model's side (cut context, hold the model), or tools the agent
runs in-process — that is the wrap.

Run: `uv run axor-proxy --demo --backend-url http://127.0.0.1:8400`
(`--demo` registers mock broken tools, zero creds; `--backend-url` auto-uploads
runs so Eval/Replay/Regression light up).

Modules:

| Module | Responsibility |
|---|---|
| `app.py` | routes: `/t/{tool}` passthrough, `/axor/runs` arm→claim, `/axor/runs/{id}/simulate`, `/axor/governed/spawn`, preflight/health |
| `runs.py` | run lifecycle; fault semantics from `axor_eval`; the run's governor; EvidenceCase construction |
| `vault.py` | sink-side credential injection (§14.2): opt-in per tool, fetched at call time, never cached, fail-closed. `check_vault_colocation` refuses to arm it against a backend in another failure domain — the mode's cost is that backend's uptime, and §14.2 answers availability with co-location |
| `run_cli.py` | `axor-proxy run -- <cmd>`: arm, execute a CLI agent, submit its stdout as the claim. Not a wrapper — it gates nothing; wrapping is what `axor_wrap.WrappedToolset` does to tool callables |
| `faults.py` / `mock_tools.py` | fault application; demo mock tools |
| `agent.py` | scripted agent — drives a run to completion over the proxy's own HTTP surface (the in-app "run an experiment") |
| `recorder.py` / `upload.py` | JSONL trace writer; best-effort upload + auto-pin to the backend |
| `governed.py` | **demo governed node**: a real `axor_core` IntentLoop (per-value taint enforcement) that produces an adapter-fidelity trace and stays live on the plane (heartbeats + desired-state), so Control shows it and interventions reach it |

Endpoints are disarmed (503) unless a run is armed; the trace file on disk is the
system of record even if upload fails.

Tests: `uv run pytest packages/axor-proxy`.
