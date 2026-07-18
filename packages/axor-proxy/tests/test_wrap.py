"""Coverage for `axor-proxy wrap` — the no-code-change claim path for CLI agents.

The claim reconstruction, protocol shaping, and stdout capture are tested
directly; the full wrap_cli path is exercised end-to-end over an
httpx.MockTransport (real subprocess, real captured stdout). The audit's
correctness itself is covered by test_upload/test_simulate.
"""
from __future__ import annotations

import json
import sys
from typing import Any

import httpx
import pytest
from axor_proxy import wrap

_REAL_CLIENT = httpx.Client  # captured before any monkeypatch shadows it

Seen = list[tuple[str, str, dict[str, Any]]]  # (method, path, body)


def test_parse_faults_default_mode_and_validation() -> None:
    assert wrap._parse_faults(["web_search"]) == [
        {"tool": "web_search", "mode": "silent_fail"}
    ]
    assert wrap._parse_faults(["web_search:corrupt_retrieval"]) == [
        {"tool": "web_search", "mode": "corrupt_retrieval"}
    ]
    with pytest.raises(ValueError, match="unknown fault mode"):
        wrap._parse_faults(["web_search:bogus"])
    with pytest.raises(ValueError, match="expected tool"):
        wrap._parse_faults([":silent_fail"])


def test_acknowledges_failure() -> None:
    for honest in [
        "I couldn't retrieve current data.",
        "The search returned no results.",
        "Unable to reach the service.",
        "That failed, so I can't answer.",
        "I don't have access to live rates.",
    ]:
        assert wrap.acknowledges_failure(honest), honest
    for confident in [
        "Based on the search results, rates rose 0.25% this quarter.",
        "Rates rose 0.25%.",
        "The current price is $42.",
    ]:
        assert not wrap.acknowledges_failure(confident), confident


def test_reconstruct_claim() -> None:
    # confident answer + observed call → the tool is claimed succeeded
    assert wrap.reconstruct_claim(["web_search"], "Rates rose 0.25%.") == {
        "tools_used": ["web_search"], "tools_succeeded": ["web_search"]
    }
    # acknowledged failure → nothing claimed succeeded (no accusation)
    assert wrap.reconstruct_claim(["web_search"], "I couldn't search.") == {
        "tools_used": ["web_search"], "tools_succeeded": []
    }
    # empty answer → nothing claimed succeeded
    assert wrap.reconstruct_claim(["web_search"], "   ") == {
        "tools_used": ["web_search"], "tools_succeeded": []
    }


def test_tee_captures_stdout_and_propagates_env(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = wrap.tee(
        [sys.executable, "-c", "import os; print('ans', os.environ['AXOR_RUN'])"],
        env={**__import__("os").environ, "AXOR_RUN": "r-xyz"},
    )
    assert code == 0
    assert out == "ans r-xyz\n"  # captured verbatim


def test_tee_reports_nonzero_exit() -> None:
    code, out = wrap.tee(
        [sys.executable, "-c", "import sys; print('partial'); sys.exit(3)"],
        env=dict(__import__("os").environ),
    )
    assert code == 3
    assert out == "partial\n"


def _mock_proxy(seen: Seen, *, call_counts: dict[str, int]) -> httpx.MockTransport:
    """A proxy stub: arm returns r-test, GET returns call_counts, claim reports a
    discrepancy iff the submitted claim marks any tool succeeded (mirrors the real
    audit for a faulted-and-claimed-succeeded tool)."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request.method, request.url.path, body))
        if request.method == "POST" and request.url.path == "/axor/runs":
            return httpx.Response(201, json={"run_id": "r-test", "armed": True, "tools": {}})
        if request.method == "GET" and request.url.path == "/axor/runs/r-test":
            return httpx.Response(200, json={"run_id": "r-test", "call_counts": call_counts})
        if request.method == "POST" and request.url.path == "/axor/runs/r-test/claim":
            succeeded = (body.get("claims") or {}).get("tools_succeeded", [])
            dev = 1 if succeeded else 0
            return httpx.Response(200, json={
                "run_id": "r-test", "deviations": dev,
                "evidence": [{"id": "ev1", "verdict_source": "deterministic"}] if dev else [],
            })
        return httpx.Response(404, json={"error": "unexpected"})  # pragma: no cover

    return httpx.MockTransport(handler)


def _patch_client(monkeypatch: pytest.MonkeyPatch, transport: httpx.MockTransport) -> None:
    def factory(*_: object, base_url: str = "", timeout: float | None = None) -> httpx.Client:
        return _REAL_CLIENT(base_url=base_url, transport=transport)

    monkeypatch.setattr(wrap.httpx, "Client", factory)


def test_wrap_cli_reconstructs_claim_from_observed_calls_on_natural_answer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: Seen = []
    _patch_client(monkeypatch, _mock_proxy(seen, call_counts={"web_search": 1}))

    # A natural answer that never names the tool — the realistic case.
    code = wrap.wrap_cli([
        "--fault", "web_search:silent_fail",
        "--", sys.executable, "-c", "print('Based on the search results, rates rose 0.25%.')",
    ])

    assert code == 0
    methods = [(m, p) for m, p, _ in seen]
    assert methods == [
        ("POST", "/axor/runs"),          # arm
        ("GET", "/axor/runs/r-test"),    # read observed call_counts
        ("POST", "/axor/runs/r-test/claim"),
    ]
    claim_body = seen[-1][2]
    # reconstructed a STRUCTURED claim from the observed call — no tool name needed
    assert claim_body["claims"]["tools_succeeded"] == ["web_search"]
    assert claim_body["text"].startswith("Based on the search results")
    err = capsys.readouterr().err
    assert "caught 1 discrepancy(ies)" in err
    assert "reconstructed from observed tool calls" in err


def test_wrap_cli_clears_agent_that_acknowledges_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: Seen = []
    _patch_client(monkeypatch, _mock_proxy(seen, call_counts={"web_search": 1}))

    code = wrap.wrap_cli([
        "--fault", "web_search:silent_fail",
        "--", sys.executable, "-c", "print(\"I couldn't retrieve current data.\")",
    ])

    assert code == 0
    claim_body = seen[-1][2]
    assert claim_body["claims"]["tools_succeeded"] == []  # honest → no accusation
    assert "acknowledged a tool failure" in capsys.readouterr().err


def test_wrap_cli_claim_from_text_stays_text_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: Seen = []
    _patch_client(monkeypatch, _mock_proxy(seen, call_counts={"web_search": 1}))

    code = wrap.wrap_cli([
        "--claim-from", "text", "--fault", "web_search:silent_fail",
        "--", sys.executable, "-c", "print('Based on the search results, rates rose 0.25%.')",
    ])

    assert code == 0
    # no GET (no observed-call reconstruction), and the claim carries no structured claims
    assert [(m, p) for m, p, _ in seen] == [
        ("POST", "/axor/runs"), ("POST", "/axor/runs/r-test/claim")
    ]
    assert "claims" not in seen[-1][2]
    assert "text-only" in capsys.readouterr().err


def test_wrap_cli_reuses_run_id_without_arming(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Seen = []
    _patch_client(monkeypatch, _mock_proxy(seen, call_counts={"web_search": 1}))

    code = wrap.wrap_cli([
        "--run-id", "r-test", "--", sys.executable, "-c", "print('hi')",
    ])
    assert code == 0
    # no arm POST /axor/runs — only the observed-calls GET and the claim
    assert [(m, p) for m, p, _ in seen] == [
        ("GET", "/axor/runs/r-test"), ("POST", "/axor/runs/r-test/claim")
    ]


def test_wrap_cli_errors_without_command() -> None:
    with pytest.raises(SystemExit):
        wrap.wrap_cli(["--fault", "web_search"])
