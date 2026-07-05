"""Scripted agent — our agent, for the in-app "run an experiment" loop.

The proxy sits in front of tools; the *agent* calls them. For the user's own
agent that agent is theirs (they point it at the proxy). But the demo-mode path
(our mock tools, zero creds) and the "try it" experience need a complete,
clickable loop. This is that agent: it drives real tool calls through the real
proxy pipeline (passthrough + fault injection + observation) and submits a
final claim, exactly as an external agent would — same HTTP surface, no bypass.

It is deliberately dumb: it calls the scripted tools, then makes a claim that
*asserts success* on the tools it called. Against an injected fault that is a
fabrication, and the audit layer catches it deterministically — reproducing the
demo's caught lie live, through the whole stack.
"""
from __future__ import annotations

from typing import Any

import httpx


def default_script(faults: list[dict[str, Any]], tools: list[str]) -> list[dict]:
    """A tool call per faulted tool (so the fault fires), else one call per
    known tool. Each step is a GET with a plausible query."""
    faulted = [f["tool"] for f in faults]
    targets = faulted or tools[:1]
    return [{"tool": t, "params": {"q": "quarterly rates"}} for t in targets]


def default_claim(faults: list[dict[str, Any]]) -> dict[str, Any]:
    """A fabricating claim: assert every faulted tool succeeded. Against a
    silent_fail / substitution that is a fabricated tool result; against a
    clean run it is simply true."""
    tools = [f["tool"] for f in faults]
    return {
        "text": "Based on the tool results, the answer is confirmed.",
        "claims": {"tools_succeeded": tools, "tools_used": tools},
    }


class ScriptedAgent:
    """Drives a run to completion over the proxy's own HTTP surface."""

    def __init__(self, base_url: str, client: httpx.AsyncClient) -> None:
        self._base = base_url.rstrip("/")
        self._client = client

    async def run(
        self, run_id: str, script: list[dict], claim: dict[str, Any]
    ) -> dict[str, Any]:
        for step in script:
            tool = step["tool"]
            params = step.get("params") or {}
            # A real agent sends its own Authorization; the proxy passes it
            # through byte-for-byte. We send a placeholder to exercise that path.
            await self._client.get(
                f"{self._base}/t/{tool}/",
                params=params,
                headers={"Authorization": "Bearer scripted-agent-token"},
            )
        resp = await self._client.post(
            f"{self._base}/axor/runs/{run_id}/claim", json=claim
        )
        return resp.json()
