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

Honesty note: these recipes are complete and runnable, but exercising them
end-to-end needs the framework installed AND an LLM API key, so they are not
part of this repo's CI. The framework-free equivalent (the scripted agent)
runs in CI on every push, and `axor_hook.py`'s tracking/claim logic is covered
independently of any framework.
