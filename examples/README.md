# Recipes — putting Axor in front of real agent frameworks

Two integration depths (see the depth ladder):

- **proxy (observe-only, no code change)**: point the framework's tool base
  URLs at `http://<proxy>/t/<tool>/`. Auth passes through byte-for-byte; every
  call is observed; armed faults inject. These recipes show exactly that line.
- **adapter (full governance)**: wrap the agent in an axor-core Invokable —
  see `axor-core`'s README; that's what unlocks live Control.

## The claim — the one thing the proxy can't see

The proxy is genuinely observe-only: it sees the **tool boundary** — which tool
was called and what came back (a real result, an injected fault, an empty body).
It cannot see the agent's own report: the final answer, and which calls the
agent treated as successful. That report is the **claim**, and the gap between
it and the observed tool reality *is* the EvidenceCase. So on your own agent
there is one small thing to wire up beyond the base URL: submit the claim.

`axor_hook.py` does it automatically. `AxorRun.tool(name)` wraps a tool
callable and records real usage; `AxorRun.submit(final_output)` posts the
agent's answer plus the derived `tools_used` / `tools_succeeded` to
`POST /axor/runs/{run_id}/claim`. Success is *derived from what happened* (a
call that returned without raising = the agent perceived success), not asserted
by a hand-written dict that would keep claiming success even when the tool
faulted — which is the very lie we're here to catch.

Drop it into *your own* loop, framework or not:

```python
from axor_hook import AxorRun

axor = AxorRun(run_id)              # run_id from POST /axor/runs

@axor.tool("web_search")           # wrap each tool the agent calls
def web_search(q: str) -> str:
    r = httpx.get(f"{axor.base}/t/web_search/", params={"q": q}, headers=axor.headers)
    r.raise_for_status()
    return r.text

try:
    answer = run_my_agent()
finally:
    axor.submit(answer)            # in finally: a partial run still records a claim
```

`AxorRun.submit` is idempotent and best-effort — a proxy that's down logs a line
and never breaks your agent. Configure the proxy URL with `AXOR_PROXY_URL`
(or `AXOR_PROXY_PORT`); it defaults to `http://127.0.0.1:8401`.

The two framework recipes (`openai_agents_proxy.py`, `crewai_proxy.py`) are just
this pattern with the framework's own tool decorator layered on top — the
wrapper preserves the tool's name, docstring and signature, so the framework's
schema extraction is unaffected.

## No code at all — `axor-proxy wrap` for a CLI agent

If your agent is something you *run* — a CLI, a script, anything that prints its
answer to stdout — you don't need to touch its code. Point its tools at a
running proxy, then wrap the invocation:

```bash
axor-proxy --demo &                                          # a proxy is running
axor-proxy wrap --fault web_search:silent_fail -- my-agent "what did rates do?"
```

`wrap` arms a run, runs the command (streaming its output through untouched),
captures stdout, and submits the claim — then prints whether a discrepancy was
caught. It sets `AXOR_RUN` / `AXOR_PROXY_URL` in the child's environment, so a
cooperating tool binds to the right run; on a single-run proxy the tool traffic
binds automatically. Reuse an already-armed run with `--run-id`; omit `--fault`
for an observe-only run. Use `wrap` **or** the in-code hook, not both.

How detection works (`--claim-from observed`, the default): a real agent never
names its tools in the answer — it says *"Based on the search results, rates rose
0.25%"*, not *"web_search returned…"* — so matching the answer text is useless.
Instead `wrap` reconstructs the claim from what the proxy **observed**: the tools
the agent actually called. If the answer doesn't acknowledge a failure, those
calls are submitted as `tools_succeeded` (the agent proceeded as though they
worked), and the audit compares that against the faults it saw —
**deterministically, independent of phrasing.** If the answer *does* own the
failure (*"I couldn't retrieve current data"*), nothing is claimed succeeded and
the agent is cleared. Live:

```
$ axor-proxy wrap --fault web_search:silent_fail -- my-agent "what did rates do?"
Based on the search results, rates rose 0.25% this quarter.
[axor] ⚠ caught 1 discrepancy (deterministic) (claim reconstructed from observed
       tool calls) — 1 EvidenceCase for run …
```

Caveat — multi-tool agents: `wrap` sees *that* several tools were called, not
*which one* the answer leaned on, so if one of several tools faulted and the
answer stays confident, `wrap` can over-attribute (a false positive when that
tool wasn't the real source). The in-code hook has no such ambiguity — it knows
each call's outcome — so prefer it for multi-tool or high-stakes agents.
`--claim-from text` forces the old text-only claim (narrow: keys on the tool name
in the answer).

Honesty note: these recipes are complete and runnable, but exercising them
end-to-end needs the framework installed AND an LLM API key, so they are not
part of this repo's CI. The framework-free equivalent (the scripted agent)
runs in CI on every push, and `axor_hook.py`'s tracking/claim logic is covered
independently of any framework.
