"""Governed demo node — a REAL axor-core IntentLoop enforcing gates on a node
that connects to the control plane.

The proxy is observe-only; this is the other half of the loop. A governed session
runs mock tools through the actual IntentLoop (per-value taint enforcement), so it
produces adapter-fidelity governance verdicts — including a recorded taint denial
when a web-tainted value is pushed at an egress sink. The trace is bridged to the
kernel event schema (the same one Replay/Regression consume) and uploaded.

A PlaneClient (axor-wrap's `axor_wrap.plane` — the kernel does not ship a plane
client) then keeps the node live: it heartbeats to the backend (so Control's
topology shows a real node with its level/budget) and subscribes to desired state
(so an operator's pause / stop / replan / inject actually reaches this node —
admission holds or winds the loop down at the intent boundary).
"""
from __future__ import annotations

import asyncio
import contextlib
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
from axor_core.contracts.trace import TraceEventKind
from axor_core.kernel.events import SCHEMA_VERSION
from axor_core.node.intent_loop import IntentLoop
from axor_core.taint.engine import TaintEngine

# The plane client lives in axor-wrap, not in the kernel: axor-core keeps only
# the primitives the plane steers through (the desired-state lattice, JCS
# canonical bytes, the event schema, the AdmissionController contract), so it
# cannot depend on a plane client at all. Same protocol code as before, one
# package over.
from axor_wrap.plane.admission import PlaneAdmission
from axor_wrap.plane.client import PlaneClient
from axor_wrap.plane.session import PlaneSession

# The governed flow, single source of truth: (tool, {arg: value}, output|None).
# web_search reads external (untrusted) content; summarize derives a value FROM
# it (the taint-graph edge); slack_post pushes the untrusted value at the egress
# sink — the step the per-value taint gate denies.
QUERY = "quarterly rates"
WEB_OUT = "EXTERNAL: quarterly rates rose 4% (unverified web content)"
SUMMARY = "SUMMARY: rates rose ~4% this quarter (derived from the web result)"

_FLOW: list[tuple[str, dict[str, str], str | None]] = [
    ("web_search", {"q": QUERY}, WEB_OUT),
    ("summarize", {"text": WEB_OUT}, SUMMARY),
    ("slack_post", {"text": WEB_OUT}, None),  # tainted value → egress → DENY
]
_TOOL_OUTPUT: dict[str, str] = {
    "web_search": WEB_OUT, "summarize": SUMMARY, "slack_post": "posted",
}
# Readable provenance refs (keyed by content) for the taint graph, so a value a
# later call carries resolves to the SAME node as the call that produced it.
_REF: dict[str, str] = {QUERY: "v_query", WEB_OUT: "v_web_result", SUMMARY: "v_summary"}
_UNTRUSTED = frozenset({"web_search"})
_EGRESS = frozenset({"slack_post"})


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
    for name, out in _TOOL_OUTPUT.items():
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
        allowed_tools=frozenset(_TOOL_OUTPUT), allow_children=False,
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
    """Emit the flow's tool calls as executor events for the IntentLoop."""
    async def stream():  # noqa: ANN202
        for i, (tool, args, _out) in enumerate(_FLOW):
            yield ExecutorEvent(
                kind=ExecutorEventKind.TOOL_USE,
                payload={"tool": tool, "args": args, "tool_use_id": f"{node_id}-{i}"},
                node_id=node_id,
            )
        yield ExecutorEvent(
            kind=ExecutorEventKind.STOP, payload={"usage": {}}, node_id=node_id,
        )
    return stream()


def _ordered_verdicts(trace_events: list) -> list[tuple[bool, str, str | None]]:
    """One (approved, reason, gate) per tool call, in call order — read from the
    REAL IntentLoop trace, so the verdicts are authentic governance decisions.

    The gate is the kernel's own name for the gate that denied, mapped from the
    category the IntentLoop recorded. It used to be the literal string
    "taint_enforcement" on every denial: a CATEGORY, not a gate name, so it was
    outside the vocabulary a recorded verdict may name (`GATE_OF_CATEGORY`'s
    values) — and it was a guess besides, since a capability or budget denial
    would have been labelled taint too. `gate_of` raises on a category it does
    not know, which is the posture the kernel chose: an unrecognised denial
    should be a loud error, not a category leaking into a field that takes a
    gate name.
    """
    from axor_core.governor import gate_of

    out: list[tuple[bool, str, str | None]] = []
    for event in trace_events:
        kind = getattr(event, "kind", None)
        if kind in (TraceEventKind.INTENT_APPROVED, TraceEventKind.INTENT_TRANSFORMED):
            out.append((True, "", None))
        elif kind is TraceEventKind.INTENT_DENIED:
            payload = getattr(event, "payload", None) or {}
            category = str(payload.get("category") or "capability")
            out.append((False, getattr(event, "reason", ""), gate_of(category)))
    return out


def _ev(seq: int, node_id: str, kind: str, verdict: str | None, **payload: Any) -> dict:  # noqa: ANN401
    return {
        # The kernel's constant, not a literal: the control plane now refuses a
        # line whose major it cannot read (limits.check_batch), so a hardcoded
        # "1.0" would turn the next kernel bump into a silent upload outage.
        "schema_version": SCHEMA_VERSION, "seq": seq, "node_id": node_id, "kind": kind,
        "ts": f"seq:{seq}", "causal_root": None,
        "gate": payload.pop("gate", None), "verdict": verdict, "payload": payload,
    }


def _build_lines(
    node_id: str, verdicts: list[tuple[bool, str, str | None]]
) -> list[dict]:
    """Serialise the flow into kernel-schema events, enriched with provenance:
    each call carries arg_refs (the value refs it reads) and, when it produces a
    value, a following tool_result with that value_ref — so the taint graph folds
    the real derivation (v_query → v_web_result → v_summary). The verdicts are the
    IntentLoop's own; only the value refs are annotated here."""
    lines: list[dict] = []
    seq = 0
    for (tool, args, output), (approved, reason, gate) in zip(_FLOW, verdicts):
        arg_refs = {a: _REF[v] for a, v in args.items() if v in _REF}
        normalized = (
            {"destination_kind": "external_domain"} if tool in _EGRESS else {}
        )
        lines.append(_ev(
            seq, node_id, "tool_call", "pass" if approved else "deny",
            tool=tool, args=args, arg_refs=arg_refs, normalized=normalized,
            gate=gate,
            **({"reason": reason} if reason else {}),
        ))
        seq += 1
        if approved and output is not None and output in _REF:
            lines.append(_ev(
                seq, node_id, "tool_result", None, tool=tool,
                value_ref=_REF[output],
                root={"sources": ["web"] if tool in _UNTRUSTED else [],
                      "sensitive": False},
            ))
            seq += 1
    return lines


async def run_governed_session(
    node_id: str, session: PlaneSession,
) -> tuple[list[dict], int]:
    """Run the flow through the REAL IntentLoop for authentic verdicts, then
    serialise adapter-fidelity events (recorded verdicts + value provenance).
    Returns the kernel-schema event lines and the number of recorded denials."""
    trace_events: list = []
    loop = IntentLoop(
        capability_executor=_executor(),
        trace_events=trace_events,
        taint_engine=TaintEngine(),
        admission=PlaneAdmission(session),
        egress_sinks=_EGRESS,
        untrusted_sources=_UNTRUSTED,
    )
    async for _ in loop.run(_scripted_stream(node_id), _envelope(node_id)):
        pass
    verdicts = _ordered_verdicts(trace_events)
    lines = _build_lines(node_id, verdicts)
    denials = sum(1 for line in lines if line.get("verdict") == "deny")
    return lines, denials


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
        # Surface a failed upload (auth, backend down) instead of reporting a
        # governed node whose trace never landed — the route turns this into 502.
        (await client.post(
            f"{base}/v1/ingest/{run_id}",
            json={"node_id": node_id, "scenario": "governed", "events": lines},
            headers=headers,
        )).raise_for_status()
        if denials:
            evidence = [{
                "scenario": "governed",
                "deviation": "tainted_value_exfiltrated",
                "verdict_source": "kernel",
                "confidence": 1.0,
                # The gate NAME the trace recorded, not the internal
                # category — a receipt and a replay step must agree.
                "observed_reality": {"gate": "taint_floor", "sink": "slack_post"},
                "agent_claim": "notified the channel with the latest rates",
                "fault_attribution": [{"fault_mode": "instruction_injection",
                                       "tool_name": "web_search", "influence": "strong"}],
            }]
            (await client.post(
                f"{base}/v1/runs/{run_id}/evidence",
                json={"node_id": node_id, "scenario": "governed", "evidence": evidence},
                headers=headers,
            )).raise_for_status()

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


# ── Governed TREE (spec v2 Ch.4): three real IntentLoop nodes, one bus ─────────
#
# orchestrator ─spawns→ researcher ─spawns→ web-scraper. The scraper reads
# untrusted web content (REAL per-value taint at its own engine); the value is
# delegated UP through the axor-core message bus — labels ride in the envelope
# and fold into each receiver's ledger (carried, not laundered). The
# orchestrator's slack_post is then denied by ITS OWN IntentLoop: containment
# at the export boundary, decided by authentic kernel gates at every node —
# no canned verdicts anywhere.

_TREE_TOOLS: dict[str, dict[str, str]] = {
    "scraper": {"web_search": WEB_OUT},
    "research": {"summarize": SUMMARY},
    "orch": {"slack_post": "posted"},
}


def _tree_executor(tools: dict[str, str]) -> CapabilityExecutor:
    ex = CapabilityExecutor()
    for name, out in tools.items():
        ex.register(_Tool(name, out))
    return ex


async def _run_node_flow(
    node_id: str,
    tools: dict[str, str],
    flow: list[tuple[str, dict[str, str]]],
    taint: TaintEngine,
    *,
    egress: frozenset[str] = frozenset(),
    untrusted: frozenset[str] = frozenset(),
) -> list[tuple[str, dict[str, str], bool, str, str | None]]:
    """One node's calls through a REAL IntentLoop over its OWN taint engine.
    Returns (tool, args, approved, reason, gate) per call, in order."""
    trace_events: list = []
    loop = IntentLoop(
        capability_executor=_tree_executor(tools),
        trace_events=trace_events,
        taint_engine=taint,
        egress_sinks=egress,
        untrusted_sources=untrusted,
    )

    async def stream():  # noqa: ANN202
        for i, (tool, args) in enumerate(flow):
            yield ExecutorEvent(
                kind=ExecutorEventKind.TOOL_USE,
                payload={"tool": tool, "args": args,
                         "tool_use_id": f"{node_id}-{i}"},
                node_id=node_id,
            )
        yield ExecutorEvent(
            kind=ExecutorEventKind.STOP, payload={"usage": {}}, node_id=node_id,
        )

    async for _ in loop.run(stream(), _envelope(node_id)):
        pass
    verdicts = _ordered_verdicts(trace_events)
    return [
        (tool, args, approved, reason, gate)
        for (tool, args), (approved, reason, gate) in zip(flow, verdicts)
    ]


async def run_governed_tree(prefix: str) -> tuple[list[dict], int, dict[str, str]]:
    """The 3-node governed tree. Returns (kernel-schema lines, denial count,
    {role: node_id}). Every verdict is an IntentLoop decision; every message
    event comes from the axor-core bus (labels in the envelope)."""
    import json as _json

    from axor_core.kernel.events import event_to_json_line
    from axor_core.node.messaging import InMemoryMessageBus, make_envelope

    ids = {r: f"{prefix}-{r}" for r in ("orch", "research", "scraper")}
    engines = {r: TaintEngine(node_id=ids[r]) for r in ids}

    # Per-node event streams, in causal order; seq assigned per node at the end.
    per_node: dict[str, list[dict]] = {nid: [] for nid in ids.values()}

    def emit_bus(event) -> None:  # noqa: ANN001 — kernel Event
        per_node[event.node_id].append(_json.loads(event_to_json_line(event)))

    bus = InMemoryMessageBus(emit=emit_bus)
    for r, nid in ids.items():
        bus.register(nid, engines[r])

    def tool_line(nid: str, tool: str, args: dict, approved: bool, reason: str,
                  gate: str | None, *, arg_refs: dict | None = None,
                  egress: bool = False) -> None:
        per_node[nid].append(_ev(
            0, nid, "tool_call", "pass" if approved else "deny",
            tool=tool, args=args, arg_refs=arg_refs or {},
            normalized={"destination_kind": "external_domain"} if egress else {},
            gate=gate,
            **({"reason": reason} if reason else {}),
        ))

    def result_line(nid: str, tool: str, ref: str, tainted: bool) -> None:
        per_node[nid].append(_ev(
            0, nid, "tool_result", None, tool=tool, value_ref=ref,
            root={"sources": ["web"] if tainted else [], "sensitive": False},
        ))

    # spawn events at the parents (structure derives from these)
    per_node[ids["orch"]].append(_ev(
        0, ids["orch"], "node_spawned", None, child_id=ids["research"],
        parent_id=ids["orch"], depth=1, edge_kind="delegation",
    ))
    per_node[ids["research"]].append(_ev(
        0, ids["research"], "node_spawned", None, child_id=ids["scraper"],
        parent_id=ids["research"], depth=2, edge_kind="delegation",
    ))

    # scraper: real untrusted read → value tainted in ITS engine
    for tool, args, ok, reason, gate in await _run_node_flow(
        ids["scraper"], _TREE_TOOLS["scraper"],
        [("web_search", {"q": QUERY})],
        engines["scraper"], untrusted=frozenset({"web_search"}),
    ):
        tool_line(ids["scraper"], tool, args, ok, reason, gate)
        if ok:
            result_line(ids["scraper"], tool, "v_web_result", tainted=True)

    # delegation up: labels ride in the envelope, fold at the researcher
    bus.send(make_envelope(engines["scraper"], ids["scraper"], ids["research"],
                           "delegation", WEB_OUT, value_ref="v_web_result"))

    # researcher: derives a summary FROM the carried value (real derivation)
    for tool, args, ok, reason, gate in await _run_node_flow(
        ids["research"], _TREE_TOOLS["research"],
        [("summarize", {"text": WEB_OUT})],
        engines["research"],
    ):
        tool_line(ids["research"], tool, args, ok, reason, gate,
                  arg_refs={"text": "v_web_result"})
        if ok:
            # the summary derives from the tainted input — register honestly
            engines["research"].register_value(
                SUMMARY, engines["research"].derive_value(WEB_OUT)
            )
            result_line(ids["research"], tool, "v_summary",
                        tainted=engines["research"].derive_value(SUMMARY).is_tainted)

    bus.send(make_envelope(engines["research"], ids["research"], ids["orch"],
                           "delegation", SUMMARY, value_ref="v_summary"))

    # orchestrator: its OWN IntentLoop denies the export of the carried value
    for tool, args, ok, reason, gate in await _run_node_flow(
        ids["orch"], _TREE_TOOLS["orch"],
        [("slack_post", {"text": SUMMARY})],
        engines["orch"], egress=frozenset({"slack_post"}),
    ):
        tool_line(ids["orch"], tool, args, ok, reason, gate,
                  arg_refs={"text": "v_summary"}, egress=True)

    # assign per-node seqs in causal order and flatten
    lines: list[dict] = []
    for nid, seq_events in per_node.items():
        for i, line in enumerate(seq_events):
            line["seq"] = i
            line["node_id"] = nid
            lines.append(line)
    denials = sum(1 for line in lines if line.get("verdict") == "deny")
    return lines, denials, ids


async def spawn_governed_tree(
    backend_url: str, ingest_key: str | None = None,
) -> dict[str, Any]:
    """Run the real governed tree and upload its multi-node trace + the
    CONTAINED EvidenceCase (anchored at the orchestrator's denied export)."""
    prefix = f"gov-tree-{secrets.token_hex(2)}"
    run_id = f"govtree_{secrets.token_hex(4)}"
    lines, denials, ids = await run_governed_tree(prefix)

    anchor_seq = next(
        (line["seq"] for line in lines
         if line["node_id"] == ids["orch"] and line.get("verdict") == "deny"),
        None,
    )
    base = backend_url.rstrip("/")
    headers = {"Authorization": f"Bearer {ingest_key}"} if ingest_key else {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        (await client.post(
            f"{base}/v1/ingest/{run_id}",
            json={"node_id": ids["orch"], "scenario": "governed-tree",
                  "events": lines},
            headers=headers,
        )).raise_for_status()
        if denials and anchor_seq is not None:
            evidence = [{
                "scenario": "governed-tree",
                "deviation": "fabrication_contained",
                "verdict_source": "kernel",
                "confidence": 1.0,
                "observed_reality": {"tool": "web_search",
                                     "origin": ids["scraper"],
                                     "carried": "web taint over two hops"},
                "agent_claim": SUMMARY,
                "fault_attribution": [{"fault_mode": "untrusted_read",
                                       "tool_name": "web_search",
                                       "influence": "strong"}],
                "anchor": {"node_id": ids["orch"], "seq": anchor_seq},
                "twin_ref": None,
            }]
            (await client.post(
                f"{base}/v1/runs/{run_id}/evidence",
                json={"node_id": ids["orch"], "scenario": "governed-tree",
                      "evidence": evidence},
                headers=headers,
            )).raise_for_status()
    return {"run_id": run_id, "nodes": ids, "denials": denials,
            "events": len(lines)}
