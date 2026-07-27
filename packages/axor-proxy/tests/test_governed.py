"""The governed demo node runs a REAL axor-core IntentLoop: a web-tainted value
pushed at an egress sink is denied by the per-value taint gate, and the trace
bridges to the kernel event schema with the recorded verdict."""
from __future__ import annotations

import pytest
from axor_proxy.governed import run_governed_session
from axor_wrap.plane.session import PlaneSession


@pytest.mark.anyio
async def test_governed_session_records_a_taint_denial() -> None:
    session = PlaneSession(node_id="gov-test", test_bench=True)
    lines, denials = await run_governed_session("gov-test", session)

    assert denials == 1
    # The denied step is a real taint_enforcement verdict on the egress sink.
    denial = next(line for line in lines if line.get("verdict") == "deny")
    assert denial["kind"] == "tool_call"
    assert denial["gate"] == "taint_enforcement"
    assert denial["payload"]["tool"] == "slack_post"
    # It carries the provenance of the value it tried to exfiltrate.
    assert denial["payload"]["arg_refs"] == {"text": "v_web_result"}

    # Every event carries a unique seq (the backend dedups on it).
    seqs = [line["seq"] for line in lines]
    assert len(seqs) == len(set(seqs))


@pytest.mark.anyio
async def test_governed_trace_carries_provenance_for_the_taint_graph() -> None:
    session = PlaneSession(node_id="gov-test", test_bench=True)
    lines, _ = await run_governed_session("gov-test", session)

    # tool_result events carry value_ref; the derivation web_result → summary is
    # exactly the arg_refs → value_ref edge the taint graph folds.
    results = {
        line["payload"]["value_ref"]
        for line in lines if line["kind"] == "tool_result"
    }
    assert {"v_web_result", "v_summary"} <= results
    summarize = next(
        line for line in lines
        if line["kind"] == "tool_call" and line["payload"]["tool"] == "summarize"
    )
    assert summarize["payload"]["arg_refs"] == {"text": "v_web_result"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
