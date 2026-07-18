"""Auto-claim hook — capture the one thing the observe-only proxy can't see.

The proxy sees the *tool boundary*: which tool was called, and what came back
(a real result, an injected fault, an empty body). What it can't see is the
agent's own report — the final answer, and which calls the agent treated as
successful. That report is the **claim**, and the discrepancy between it and the
observed tool reality is the EvidenceCase.

Submitting a claim by hand is fragile: a static ``tools_succeeded=["web_search"]``
asserts success even when the tool faulted — which is exactly the lie Axor
exists to catch. This helper derives the claim from what actually happened:

- ``tools_used``      — every wrapped tool the agent actually invoked, in order.
- ``tools_succeeded`` — the ones that returned without raising (i.e. the agent
  perceived success). A deprived/empty result that the framework surfaces as a
  normal return still counts as "the agent thought it worked" — and that is the
  claim we want to contrast against the proxy's observation.
- ``text``            — the agent's final output, submitted verbatim.

Framework-agnostic: ``tool()`` wraps any plain callable, so you can drop this
into your own agent loop, not just the two framework recipes here.

    from axor_hook import AxorRun

    axor = AxorRun(run_id)          # run_id from POST /axor/runs

    @axor.tool("web_search")        # wrap the callable the agent invokes
    def web_search(q: str) -> str:
        r = httpx.get(f"{axor.base}/t/web_search/", params={"q": q},
                      headers=axor.headers)
        r.raise_for_status()
        return r.text

    try:
        answer = run_my_agent()
    finally:
        axor.submit(answer)         # in finally: a partial run still records a claim
"""
from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any, TypeVar

import httpx

F = TypeVar("F", bound=Callable[..., Any])

DEFAULT_BASE = os.environ.get(
    "AXOR_PROXY_URL", f"http://127.0.0.1:{os.environ.get('AXOR_PROXY_PORT', '8401')}"
)


class AxorRun:
    """Tracks real tool usage and submits the agent's claim for auditing.

    One instance per armed run. Thread-unsafe by design (an agent run is a
    single logical thread of tool calls); spin up one per concurrent run.
    """

    def __init__(self, run_id: str, base_url: str = DEFAULT_BASE) -> None:
        self.run_id = run_id
        self.base = base_url.rstrip("/")
        self._used: list[str] = []
        self._succeeded: set[str] = set()
        self._submitted = False

    @property
    def headers(self) -> dict[str, str]:
        """Pin tool calls to this run on a shared proxy. Empty run_id ⇒ no header
        (single-run proxy binds the active run itself)."""
        return {"X-Axor-Run": self.run_id} if self.run_id else {}

    def tool(self, name: str) -> Callable[[F], F]:
        """Wrap a tool callable so its use/success is recorded for the claim.

        The wrapper is transparent — ``functools.wraps`` preserves the name,
        docstring and signature, so framework decorators layered on top
        (``@function_tool``, ``@tool(...)``) still read the real schema."""

        def decorate(fn: F) -> F:
            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - wraps an arbitrary tool
                self._used.append(name)
                out = fn(*args, **kwargs)      # raises ⇒ agent saw a failure
                self._succeeded.add(name)      # returned ⇒ agent perceived success
                return out

            return wrapper  # type: ignore[return-value]

        return decorate

    def submit(self, final_output: Any, *, token_count: int | None = None) -> None:  # noqa: ANN401 - agent's final answer, any type
        """Submit the agent's final answer + derived tool claims. Idempotent and
        best-effort: a proxy that's down never breaks the agent run."""
        if not self.run_id or self._submitted:
            return
        self._submitted = True
        claims: dict[str, Any] = {
            "tools_used": list(dict.fromkeys(self._used)),  # de-duped, order kept
            "tools_succeeded": sorted(self._succeeded),
        }
        if token_count is not None:
            claims["token_count"] = token_count
        try:
            httpx.post(
                f"{self.base}/axor/runs/{self.run_id}/claim",
                json={"text": str(final_output), "claims": claims},
                timeout=10,
            )
        except httpx.HTTPError as exc:  # observability overlay: never fatal
            print(f"[axor] claim not submitted ({exc}); run continues", flush=True)
