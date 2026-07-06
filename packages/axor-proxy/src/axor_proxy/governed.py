"""Governed demo node — a REAL axor-core IntentLoop enforcing gates on a node
that connects to the control plane.

The proxy is observe-only; this is the other half of the loop. A governed session
runs mock tools through the actual IntentLoop (per-value taint enforcement), so it
produces adapter-fidelity governance verdicts — including a recorded taint denial
when a web-tainted value is pushed at an egress sink. The trace is bridged to the
kernel event schema (the same one Replay/Regression consume) and uploaded.

A PlaneClient then keeps the node live: it heartbeats to the backend (so Control's
topology shows a real node with its level/budget) and subscribes to desired state
(so an operator's pause / stop / replan / inject actually reaches this node —
admission holds or winds the loop down at the intent boundary).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
from typing import Any

import httpx
from axor_core.capability.executor import CapabilityExecutor, ToolHandler
from axor_core.contracts.cancel import make_token
from axor_core.contracts.context import ContextView, LineageSummary
from axor_core.contracts.envelope import (
    Capabilities,
    ExecutionEnvelope,
    ExportContract,
)
from axor_core.contracts.policy import ExecutionPolicy, ExportMode, ToolPolicy
from axor_core.contracts.result import ExecutorEvent, ExecutorEventKind
from axor_core.kernel.events import event_to_json_line
from axor_core.node.intent_loop import IntentLoop
from axor_core.plane.admission import PlaneAdmission
from axor_core.plane.bridge import trace_to_kernel
from axor_core.plane.client import PlaneClient
from axor_core.plane.session import PlaneSession
from axor_core.taint.engine import TaintEngine

# The mock web read returns untrusted external content; pushing THIS value at the
# egress sink is the exfil the per-value taint gate denies.
WEB_OUT = "EXTERNAL: quarterly rates rose 4% (unverified web content)"

TOOLS: dict[str, str] = {
    "web_search": WEB_OUT,        # untrusted source
    "notes_write": "written",     # benign internal write
    "slack_post": "posted",       # egress sink
}


class _Tool(ToolHandler):
    def __init__(self, name: str, output: Any) -> None:  # noqa: ANN401
        self._n, self._o = name, output

    @property
    def name(self) -> str:
        return self._n

    async def execute(self, args: dict[str, Any]) -> Any:  # noqa: ANN401
        return self._o


def _executor() -> CapabilityExecutor:
    ex = CapabilityExecutor()
    for name, out in TOOLS.items():
        ex.register(_Tool(name, out))
    return ex


def _envelope(node_id: str) -> ExecutionEnvelope:
    policy = ExecutionPolicy(
        name="governed-demo",
        tool_policy=ToolPolicy(allow_read=True, allow_write=True),
    )
    lineage = LineageSummary(
        node_id=node_id, parent_id=None, depth=0,
        ancestry_ids=[], inherited_restrictions=[],
    )
    ctx = ContextView(
        node_id=node_id, working_summary="governed demo", visible_fragments=[],
        active_constraints=[], lineage=lineage, token_count=0, compression_ratio=1.0,
    )
    caps = Capabilities(
        allowed_tools=frozenset(TOOLS), allow_children=False,
        allow_nested_children=False, allow_context_expansion=False,
        allow_export=True, allow_mutation=True, max_child_depth=0,
    )
    return ExecutionEnvelope(
        node_id=node_id, task="summarise the latest rates and notify the channel",
        context=ctx, policy=policy, capabilities=caps,
        export_contract=ExportContract(
            mode=ExportMode.FULL, allowed_fields=frozenset(["output"]),
            max_export_tokens=1024,
        ),
        lineage=lineage, cancel_token=make_token(),
    )


def _scripted_stream(node_id: str):  # noqa: ANN202
    """A governed agent's tool calls: a web read, a benign note, then an exfil —
    pushing the untrusted web value at the egress sink (the denied step)."""
    async def stream():  # noqa: ANN202
        for i, (tool, args) in enumerate([
            ("web_search", {"q": "quarterly rates"}),
            ("notes_write", {"text": "checked the rates"}),
            ("slack_post", {"text": WEB_OUT}),  # tainted value → egress → DENY
        ]):
            yield ExecutorEvent(
                kind=ExecutorEventKind.TOOL_USE,
                payload={"tool": tool, "args": args, "tool_use_id": f"{node_id}-{i}"},
                node_id=node_id,
            )
        yield ExecutorEvent(
            kind=ExecutorEventKind.STOP, payload={"usage": {}}, node_id=node_id,
        )
    return stream()


async def run_governed_session(
    node_id: str, session: PlaneSession,
) -> tuple[list[dict], int]:
    """Run one governed session through the real IntentLoop. Returns the
    kernel-schema event lines and the number of recorded denials."""
    trace_events: list = []
    loop = IntentLoop(
        capability_executor=_executor(),
        trace_events=trace_events,
        taint_engine=TaintEngine(),
        admission=PlaneAdmission(session),
        egress_sinks=frozenset({"slack_post"}),
        untrusted_sources=frozenset({"web_search"}),
    )
    async for _ in loop.run(_scripted_stream(node_id), _envelope(node_id)):
        pass
    kernel_events = trace_to_kernel(trace_events, node_id)
    lines = [json.loads(event_to_json_line(e)) for e in kernel_events]
    for line in lines:
        _label_denial(line)
    denials = sum(1 for line in lines if line.get("verdict") == "deny")
    return lines, denials


def _label_denial(line: dict) -> None:
    """The bridge's denial event names the tool inside the reason string, not as
    a payload column — lift it out so the timeline box can read the tool name."""
    payload = line.get("payload") or {}
    if line.get("kind") == "denial" and "tool" not in payload:
        match = re.search(r"'([\w.-]+)'", str(payload.get("reason", "")))
        if match:
            payload["tool"] = match.group(1)
            line["payload"] = payload


async def spawn_governed_node(
    backend_url: str, ingest_key: str | None = None,
    ttl_seconds: float = 180.0,
) -> dict[str, Any]:
    """Run a governed session, upload its trace, and keep the node live on the
    plane for `ttl_seconds` (heartbeats + desired-state subscription) so it shows
    up in Control and responds to interventions. Returns node_id + run_id."""
    node_id = f"governed-{secrets.token_hex(3)}"
    run_id = f"gov_{secrets.token_hex(4)}"
    session = PlaneSession(node_id=node_id, test_bench=True)

    lines, denials = await run_governed_session(node_id, session)

    base = backend_url.rstrip("/")
    headers = {"Authorization": f"Bearer {ingest_key}"} if ingest_key else {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(
            f"{base}/v1/ingest/{run_id}",
            json={"node_id": node_id, "scenario": "governed", "events": lines},
            headers=headers,
        )
        if denials:
            evidence = [{
                "scenario": "governed",
                "deviation": "tainted_value_exfiltrated",
                "verdict_source": "kernel",
                "confidence": 1.0,
                "observed_reality": {"gate": "taint_enforcement", "sink": "slack_post"},
                "agent_claim": "notified the channel with the latest rates",
                "fault_attribution": [{"fault_mode": "instruction_injection",
                                       "tool_name": "web_search", "influence": "strong"}],
            }]
            await client.post(
                f"{base}/v1/runs/{run_id}/evidence",
                json={"node_id": node_id, "scenario": "governed", "evidence": evidence},
                headers=headers,
            )

    # Keep the node live on the plane for the TTL. The heartbeat telemetry goes
    # to a SEPARATE run id (keyed by node) so it never pollutes the governed
    # trace's event stream — Control reads reported state (keyed by node_id), not
    # the run, so the live node still shows up.
    stop = asyncio.Event()
    client = PlaneClient(base, session, run_id=f"{node_id}-live")

    def _level() -> str:
        return "RESTRICTED" if session.stopped else ("CAUTIOUS" if session.paused else "NORMAL")

    async def _keepalive() -> None:
        subscribe = asyncio.create_task(client.run(stop))
        heartbeat = asyncio.create_task(
            client.heartbeat_loop(stop, level_fn=_level)
        )
        try:
            await client.flush(level=_level())  # first heartbeat → appears in Control now
            await asyncio.wait_for(stop.wait(), ttl_seconds)
        except TimeoutError:
            pass
        finally:
            stop.set()
            for task in (subscribe, heartbeat):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    task = asyncio.create_task(_keepalive())
    return {
        "node_id": node_id, "run_id": run_id, "events": len(lines),
        "denials": denials, "ttl_seconds": ttl_seconds, "_task": task,
    }
