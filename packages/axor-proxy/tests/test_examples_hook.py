"""Framework-free coverage for examples/axor_hook.py — the auto-claim helper.

The two framework recipes need an LLM key and aren't in CI, but the hook's
tracking + claim logic is pure Python (httpx only) and must not regress: it is
the golden path that makes "catch a lie on your own agent" honest. This exercises
it directly, no framework, no network.
"""
from __future__ import annotations

import importlib.util
import inspect
import pathlib
from typing import Any

import httpx
import pytest

# Load examples/axor_hook.py by path — examples/ is not an installed package.
_HOOK_PATH = pathlib.Path(__file__).parents[3] / "examples" / "axor_hook.py"
_spec = importlib.util.spec_from_file_location("axor_hook", _HOOK_PATH)
assert _spec and _spec.loader
axor_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(axor_hook)
AxorRun = axor_hook.AxorRun


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(
        url: str, json: dict[str, Any] | None = None, timeout: float | None = None
    ) -> None:
        calls.append((url, json or {}))

    monkeypatch.setattr(axor_hook.httpx, "post", fake_post)
    return calls


def test_wrapper_preserves_tool_signature_and_metadata() -> None:
    """Framework decorators (@function_tool, @tool) read the signature — the
    wrapper must be transparent or the tool schema breaks."""
    axor = AxorRun("r1")

    def original(q: str) -> str:
        """Search the web."""
        return "ok"

    web_search = axor.tool("web_search")(original)

    # Signature is preserved (param names + annotations), robust to PEP-563
    # stringized annotations — what a framework's schema extractor reads.
    assert list(inspect.signature(web_search).parameters) == ["q"]
    assert web_search.__annotations__ == original.__annotations__
    assert web_search.__doc__ == "Search the web."
    assert web_search.__name__ == original.__name__  # wraps copies the underlying name
    assert web_search.__wrapped__ is original  # functools.wraps chain intact


def test_success_is_derived_from_real_calls(captured: list[tuple[str, dict[str, Any]]]) -> None:
    """tools_succeeded reflects what happened: a raising tool is excluded even
    though it was used — the opposite of a static hand-written claim."""
    axor = AxorRun("r2")

    @axor.tool("web_search")
    def web_search(q: str) -> str:
        return "ok"

    @axor.tool("db_lookup")
    def db_lookup(k: str) -> str:
        raise RuntimeError("boom")

    web_search("a")
    web_search("b")  # duplicate use
    with pytest.raises(RuntimeError):
        db_lookup("k")

    axor.submit("rates rose 0.25%")

    url, payload = captured[-1]
    assert url == "http://127.0.0.1:8401/axor/runs/r2/claim"
    assert payload["text"] == "rates rose 0.25%"
    assert payload["claims"]["tools_used"] == ["web_search", "db_lookup"]  # de-duped, order kept
    assert payload["claims"]["tools_succeeded"] == ["web_search"]  # db_lookup raised → excluded


def test_submit_is_idempotent(captured: list[tuple[str, dict[str, Any]]]) -> None:
    axor = AxorRun("r3")
    axor.submit("first")
    axor.submit("second")
    assert len(captured) == 1


def test_empty_run_id_never_posts(captured: list[tuple[str, dict[str, Any]]]) -> None:
    axor = AxorRun("")
    assert axor.headers == {}
    axor.submit("x")
    assert captured == []


def test_token_count_included_when_given(captured: list[tuple[str, dict[str, Any]]]) -> None:
    axor = AxorRun("r4")
    axor.submit("answer", token_count=1234)
    assert captured[-1][1]["claims"]["token_count"] == 1234


def test_proxy_down_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """The overlay is advisory: a proxy that's down must not break the agent."""
    def boom(url: str, json: dict[str, Any] | None = None, timeout: float | None = None) -> None:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(axor_hook.httpx, "post", boom)
    AxorRun("r5").submit("y")  # must not raise
