"""Coverage for `axor-proxy wrap` — the no-code-change claim path for CLI agents.

The arm→claim protocol shaping and the stdout capture are tested directly; the
full wrap_cli path is exercised end-to-end over an httpx.MockTransport (so it
runs a real subprocess and submits the real captured stdout as the claim). The
audit's correctness itself is covered by test_upload/test_simulate.
"""
from __future__ import annotations

import sys
from typing import Any

import httpx
import pytest
from axor_proxy import wrap

_REAL_CLIENT = httpx.Client  # captured before any monkeypatch shadows it


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


def _mock_proxy(seen: list[tuple[str, dict[str, Any]]], *, deviations: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content) if request.content else {}
        seen.append((request.url.path, body))
        if request.url.path == "/axor/runs":
            return httpx.Response(201, json={"run_id": "r-test", "armed": True, "tools": {}})
        if request.url.path == "/axor/runs/r-test/claim":
            return httpx.Response(200, json={
                "run_id": "r-test",
                "evidence": [{"id": "ev1", "verdict_source": "heuristic"}] if deviations else [],
                "deviations": deviations,
                "upload": None,
            })
        return httpx.Response(404, json={"error": "unexpected"})  # pragma: no cover

    return httpx.MockTransport(handler)


def test_wrap_cli_arms_runs_and_submits_captured_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    transport = _mock_proxy(seen, deviations=1)

    def client_factory(
        *_: object, base_url: str = "", timeout: float | None = None
    ) -> httpx.Client:
        return _REAL_CLIENT(base_url=base_url, transport=transport)

    monkeypatch.setattr(wrap.httpx, "Client", client_factory)

    code = wrap.wrap_cli([
        "--fault", "web_search:silent_fail",
        "--", sys.executable, "-c", "print('rates rose 0.25%')",
    ])

    assert code == 0  # agent's own exit code
    # arm then claim, in order
    assert [p for p, _ in seen] == ["/axor/runs", "/axor/runs/r-test/claim"]
    arm_body = seen[0][1]
    assert arm_body["faults"] == [{"tool": "web_search", "mode": "silent_fail"}]
    # the captured stdout became the claim text
    assert seen[1][1]["text"] == "rates rose 0.25%\n"
    cap = capsys.readouterr()
    # the agent's stdout was streamed through to the terminal
    assert "rates rose 0.25%" in cap.out
    # the caught result reports the verdict source honestly
    assert "caught 1 discrepancy(ies) (heuristic)" in cap.err


def test_wrap_cli_reuses_run_id_without_arming(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict[str, Any]]] = []
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json={"deviations": 0, "evidence": []})
        if (seen.append((req.url.path, {})) or True)
        else httpx.Response(500)
    )

    def client_factory(
        *_: object, base_url: str = "", timeout: float | None = None
    ) -> httpx.Client:
        return _REAL_CLIENT(base_url=base_url, transport=transport)

    monkeypatch.setattr(wrap.httpx, "Client", client_factory)

    code = wrap.wrap_cli([
        "--run-id", "r-existing", "--", sys.executable, "-c", "print('hi')",
    ])
    assert code == 0
    # no /axor/runs arm call — only the claim on the reused run
    assert [p for p, _ in seen] == ["/axor/runs/r-existing/claim"]


def test_wrap_cli_errors_without_command() -> None:
    with pytest.raises(SystemExit):
        wrap.wrap_cli(["--fault", "web_search"])
