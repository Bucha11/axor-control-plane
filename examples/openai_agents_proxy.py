"""OpenAI Agents SDK behind the Axor proxy — the 20-line recipe.

The SDK's function tools call whatever URL you give them; route those calls
through the proxy and every tool interaction is observed (and faultable on a
test bench) with zero agent-code changes beyond the base URL.

    pip install openai-agents httpx
    uvx axor-proxy --demo &                        # or your tools.json
    curl -X POST localhost:8401/axor/runs \
         -d '{"scenario":"prod-shadow","faults":[]}'   # arm → note run_id
    OPENAI_API_KEY=… python examples/openai_agents_proxy.py <run_id>
"""
import sys

import httpx
from agents import Agent, Runner, function_tool  # openai-agents

AXOR = "http://127.0.0.1:8401"
RUN_ID = sys.argv[1] if len(sys.argv) > 1 else ""


@function_tool
def web_search(q: str) -> str:
    """Search the web (via the Axor-observed endpoint)."""
    # THE integration line: the tool dials the proxy, not the upstream.
    # X-Axor-Run pins the call to your armed run on a shared proxy.
    r = httpx.get(f"{AXOR}/t/web_search/", params={"q": q},
                  headers={"X-Axor-Run": RUN_ID} if RUN_ID else {})
    r.raise_for_status()
    return r.text


agent = Agent(name="researcher", instructions="Answer using web_search.",
              tools=[web_search])

if __name__ == "__main__":
    result = Runner.run_sync(agent, "What did rates do this quarter?")
    print(result.final_output)
    # Submit the agent's claim so the audit can judge it:
    httpx.post(f"{AXOR}/axor/runs/{RUN_ID}/claim", json={
        "text": result.final_output,
        "claims": {"tools_succeeded": ["web_search"], "tools_used": ["web_search"]},
    })
