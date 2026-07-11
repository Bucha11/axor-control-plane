"""CrewAI behind the Axor proxy — the 20-line recipe.

Same idea: the crew's tool dials the proxy. Observe-only in prod-shadow;
arm a fault on a test bench and watch the crew's claim get audited.

    pip install crewai httpx
    uvx axor-proxy --demo &
    curl -X POST localhost:8401/axor/runs -d '{"scenario":"crew","faults":[]}'
    OPENAI_API_KEY=… python examples/crewai_proxy.py <run_id>
"""
import sys

import httpx
from crewai import Agent, Crew, Task
from crewai.tools import tool

AXOR = "http://127.0.0.1:8401"
RUN_ID = sys.argv[1] if len(sys.argv) > 1 else ""


@tool("web_search")
def web_search(q: str) -> str:
    """Search the web (Axor-observed)."""
    r = httpx.get(f"{AXOR}/t/web_search/", params={"q": q},
                  headers={"X-Axor-Run": RUN_ID} if RUN_ID else {})
    r.raise_for_status()
    return r.text


researcher = Agent(role="researcher", goal="Answer with sourced facts",
                   backstory="Careful analyst.", tools=[web_search])
task = Task(description="What did rates do this quarter?",
            expected_output="A short sourced answer.", agent=researcher)

if __name__ == "__main__":
    out = Crew(agents=[researcher], tasks=[task]).kickoff()
    print(out)
    httpx.post(f"{AXOR}/axor/runs/{RUN_ID}/claim", json={
        "text": str(out),
        "claims": {"tools_succeeded": ["web_search"], "tools_used": ["web_search"]},
    })
