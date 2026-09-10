"""The per-request ceilings read their environment at import time.

Which makes a typo in one of them a backend that does not start, so the message
has to name the variable rather than quote the value and leave the operator to
guess which of the five it came from.
"""
from __future__ import annotations

import importlib

import pytest
from axor_backend import limits

NAMES = (
    "AXOR_MAX_EVENTS_PER_BATCH",
    "AXOR_SUBGRAPH_CACHE_MAX",
    "AXOR_MAX_PINS_PER_PACKAGE",
    "AXOR_MAX_KHOP_K",
    "AXOR_MAX_KHOP_LIMIT",
)


@pytest.mark.parametrize("name", NAMES)
def test_a_typo_names_the_variable(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(name, "ten thousand")
    with pytest.raises(ValueError, match=name):
        importlib.reload(limits)


@pytest.mark.parametrize("name", NAMES)
def test_a_ceiling_of_zero_is_refused(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero would answer 413 to every request the ceiling guards."""
    monkeypatch.setenv(name, "0")
    with pytest.raises(ValueError, match="must be >= 1"):
        importlib.reload(limits)


def test_the_defaults_stand_when_nothing_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    reloaded = importlib.reload(limits)
    assert reloaded.MAX_EVENTS_PER_BATCH == 10000
    assert reloaded.SUBGRAPH_CACHE_MAX == 512
    assert reloaded.MAX_PINS_PER_PACKAGE == 500
    assert reloaded.MAX_KHOP_K == 30
    assert reloaded.MAX_KHOP_LIMIT == 1000
