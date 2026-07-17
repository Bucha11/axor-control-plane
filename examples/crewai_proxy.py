"""CrewAI behind the Axor proxy — the 20-line recipe.

Same idea: the crew's tool dials the proxy. Observe-only in prod-shadow; arm a
fault on a test bench and watch the crew's claim get audited. ``AxorRun``
captures the crew's final output and derived tool claims automatically — no
hand-written success assertion to drift out of sync with reality.

    pip install crewai httpx
    uvx axor-proxy --demo &
    curl -X POST localhost:8401/axor/runs -d '{"scenario":"crew","faults":[]}'
    OPENAI_API_KEY=… python examples/crewai_proxy.py <run_id>
"""
import sys

import httpx
from axor_hook import AxorRun
from crewai import Agent, Crew, Task
from crewai.tools import tool

axor = AxorRun(sys.argv[1] if len(sys.argv) > 1 else "")


@tool("web_search")
@axor.tool("web_search")  # records use/success for the auto-claim
def web_search(q: str) -> str:
    """Search the web (Axor-observed)."""
    r = httpx.get(f"{axor.base}/t/web_search/", params={"q": q}, headers=axor.headers)
    r.raise_for_status()
    return r.text


researcher = Agent(role="researcher", goal="Answer with sourced facts",
                   backstory="Careful analyst.", tools=[web_search])
task = Task(description="What did rates do this quarter?",
            expected_output="A short sourced answer.", agent=researcher)

if __name__ == "__main__":
    out = None
    try:
        out = Crew(agents=[researcher], tasks=[task]).kickoff()
        print(out)
    finally:
        axor.submit(out if out else "")
