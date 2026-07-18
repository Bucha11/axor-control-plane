"""OpenAI Agents SDK behind the Axor proxy — the 20-line recipe.

The SDK's function tools call whatever URL you give them; route those calls
through the proxy and every tool interaction is observed (and faultable on a
test bench) with zero agent-code changes beyond the base URL. The one thing the
observe-only proxy can't see — the agent's final answer and which calls it
treated as successful — is captured automatically by ``AxorRun`` (see
``axor_hook.py``) and submitted as the claim the audit judges.

    pip install openai-agents httpx
    uvx axor-proxy --demo &                        # or your tools.json
    curl -X POST localhost:8401/axor/runs \
         -d '{"scenario":"prod-shadow","faults":[]}'   # arm → note run_id
    OPENAI_API_KEY=… python examples/openai_agents_proxy.py <run_id>
"""
import sys

import httpx
from agents import Agent, Runner, function_tool  # openai-agents
from axor_hook import AxorRun

axor = AxorRun(sys.argv[1] if len(sys.argv) > 1 else "")


@function_tool
@axor.tool("web_search")  # records use/success for the auto-claim
def web_search(q: str) -> str:
    """Search the web (via the Axor-observed endpoint)."""
    # THE integration line: the tool dials the proxy, not the upstream.
    # axor.headers pins the call to your armed run on a shared proxy.
    r = httpx.get(f"{axor.base}/t/web_search/", params={"q": q}, headers=axor.headers)
    r.raise_for_status()
    return r.text


agent = Agent(name="researcher", instructions="Answer using web_search.",
              tools=[web_search])

if __name__ == "__main__":
    result = None
    try:
        result = Runner.run_sync(agent, "What did rates do this quarter?")
        print(result.final_output)
    finally:
        # In a finally: even a run that errors mid-way records what it claimed.
        axor.submit(result.final_output if result else "")
