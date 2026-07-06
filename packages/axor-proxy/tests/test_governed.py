"""The governed demo node runs a REAL axor-core IntentLoop: a web-tainted value
pushed at an egress sink is denied by the per-value taint gate, and the trace
bridges to the kernel event schema with the recorded verdict."""
from __future__ import annotations

import pytest
from axor_core.plane.session import PlaneSession
from axor_proxy.governed import run_governed_session


@pytest.mark.anyio
async def test_governed_session_records_a_taint_denial() -> None:
    session = PlaneSession(node_id="gov-test", test_bench=True)
    lines, denials = await run_governed_session("gov-test", session)

    assert denials == 1
    kinds = [(line["kind"], line.get("verdict")) for line in lines]
    # web_search (pass), notes_write (pass), slack_post (deny).
    assert ("tool_call", "pass") in kinds
    assert kinds[-1] == ("denial", "deny")

    denial = next(line for line in lines if line.get("verdict") == "deny")
    assert denial["gate"] == "taint_enforcement"
    # The tool name was lifted out of the reason into a payload column.
    assert denial["payload"].get("tool") == "slack_post"
    # Every event carries a unique seq (the backend dedups on it).
    seqs = [line["seq"] for line in lines]
    assert len(seqs) == len(set(seqs))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
