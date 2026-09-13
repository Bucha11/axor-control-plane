# axor-proxy

Observe-only tool proxy (Starlette + httpx). Sits in front of the agent's tools;
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

Run: `uv run axor-proxy --demo --backend-url http://127.0.0.1:8400`
(`--demo` registers mock broken tools, zero creds; `--backend-url` auto-uploads
runs so Eval/Replay/Regression light up).

Modules:

| Module | Responsibility |
|---|---|
| `app.py` | routes: `/t/{tool}` passthrough, `/axor/runs` arm→claim, `/axor/runs/{id}/simulate`, `/axor/governed/spawn`, preflight/health |
| `runs.py` | run lifecycle; fault semantics from `axor_eval`; the run's governor; EvidenceCase construction |
| `run_cli.py` | `axor-proxy run -- <cmd>`: arm, execute a CLI agent, submit its stdout as the claim. Governs nothing — it was called `wrap`, which is axor-wrap's word for the thing that gates and taints |
| `faults.py` / `mock_tools.py` | fault application; demo mock tools |
| `agent.py` | scripted agent — drives a run to completion over the proxy's own HTTP surface (the in-app "run an experiment") |
| `recorder.py` / `upload.py` | JSONL trace writer; best-effort upload + auto-pin to the backend |
| `governed.py` | **demo governed node**: a real `axor_core` IntentLoop (per-value taint enforcement) that produces an adapter-fidelity trace and stays live on the plane (heartbeats + desired-state), so Control shows it and interventions reach it |

Endpoints are disarmed (503) unless a run is armed; the trace file on disk is the
system of record even if upload fails.

Tests: `uv run pytest packages/axor-proxy`.
