"""The canned runs are a reference shape, so they are held to the contract.

`demo.py` exists because the proxy cannot record adapter depth: these fixtures
are what the deep surfaces are demonstrated on, and what a reader copies when
asking "what does a real trace look like". A fixture that drifts from the
contract teaches the drift — the file's own docstring makes that argument about
a `normalized` block that once carried one field of ten.
"""
from __future__ import annotations

import json

import pytest
from axor_backend import demo
from axor_backend.replay_api import kernel_config_from_json
from axor_core.governor import GATE_OF_CATEGORY, gate_of
from axor_core.kernel.events import event_from_json_line
from axor_core.kernel.replay import replay

# The vocabulary a recorded verdict may name — the kernel's own table, not a
# private copy. axor-lab kept one of those and seven of eleven categories fell
# through it unmapped (axor_core/tests/test_gate_vocabulary.py).
GATE_NAMES = frozenset(GATE_OF_CATEGORY.values())

FIXTURES = {
    "EX_BLOCK": demo.EX_BLOCK_EVENTS,
    "EX_PASS": demo.EX_PASS_EVENTS,
    "TREE": demo.TREE_EVENTS,
}
ALL_EVENTS = [(name, e) for name, evs in FIXTURES.items() for e in evs]


def _events(raw: list[dict]) -> list:
    return [event_from_json_line(json.dumps(e)) for e in raw]


@pytest.mark.parametrize(
    ("name", "event"),
    [(n, e) for n, e in ALL_EVENTS if e.get("gate") is not None],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_every_recorded_gate_is_a_gate_name_not_a_category(
    name: str, event: dict,
) -> None:
    """`gate` takes a gate NAME. The denied peer send said "message_gate" — the
    denial CATEGORY, which `gate_of` maps to "message" — so it named a gate
    outside the vocabulary, which is how an unrecognised denial becomes an
    invalid trace instead of a loud error."""
    gate = event["gate"]
    hint = ""
    if gate in GATE_OF_CATEGORY:
        hint = f" — that is a category; gate_of({gate!r}) == {gate_of(gate)!r}"
    assert gate in GATE_NAMES, (
        f"{name} seq={event['seq']} node={event['node_id']}: "
        f"gate {gate!r} is not one of {sorted(GATE_NAMES)}{hint}"
    )


@pytest.mark.parametrize(
    ("name", "event"),
    [(n, e) for n, e in ALL_EVENTS if e.get("verdict") == "deny"],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_every_denial_names_the_gate_and_the_reason(name: str, event: dict) -> None:
    """A denial with no reason lets a consumer see THAT the kernel refused and
    not what over. Every real producer writes one — axor_wrap's bridge, the
    proxy, IntentLoop._record_denial."""
    assert event.get("gate"), f"{name} seq={event['seq']}: denial with no gate"
    assert event["payload"].get("reason"), (
        f"{name} seq={event['seq']}: denial with no reason"
    )


def test_no_two_events_share_a_mutable_root() -> None:
    """The roots were two module-level dicts handed into nine payloads, so one
    `append` rewrote six events of the tree at once."""
    seen: list[int] = []
    for _, event in ALL_EVENTS:
        payload = event["payload"]
        for root in (payload.get("root"), payload.get("driving_root"),
                     (payload.get("carried") or {}).get("root")):
            if root is not None:
                assert id(root) not in seen, "two events share one root object"
                seen.append(id(root))
    assert len(seen) > 5  # the fixtures really do carry roots


class TestTheFixturesReplayAsTheirDocstringsClaim:
    """The file promises specific replay behaviour; nothing checked it here."""

    def test_ex_block_reproduces_its_denial_under_the_matching_config(self) -> None:
        result = replay(_events(demo.EX_BLOCK_EVENTS),
                        kernel_config_from_json(demo.EX_CONFIG))
        assert result.first_divergence is None
        slack = next(s for s in result.steps
                     if s.event.payload.get("tool") == "slack_post")
        assert slack.reevaluated_verdict is not None
        assert slack.reevaluated_verdict.value == "deny"

    def test_ex_block_diverges_without_the_exec_capability(self) -> None:
        config = dict(demo.EX_CONFIG, allowed_tools=[
            t for t in demo.EX_CONFIG["allowed_tools"] if t != "bash"
        ])
        result = replay(_events(demo.EX_BLOCK_EVENTS),
                        kernel_config_from_json(config))
        assert result.first_divergence == 2  # the passing bash call

    def test_ex_pass_stays_clean(self) -> None:
        result = replay(_events(demo.EX_PASS_EVENTS),
                        kernel_config_from_json(demo.EX_CONFIG))
        assert result.first_divergence is None
        assert all(s.reevaluated_verdict is None
                   or s.reevaluated_verdict.value == "pass" for s in result.steps)

    def test_the_tree_contains_the_fabrication_at_the_orchestrator(self) -> None:
        result = replay(_events(demo.TREE_EVENTS),
                        kernel_config_from_json(demo.TREE_CONFIG))
        assert result.first_divergence is None
        export = next(s for s in result.steps
                      if s.event.node_id == demo.TREE_ORCH
                      and s.event.payload.get("tool") == "slack_post")
        assert export.reevaluated_verdict is not None
        assert export.reevaluated_verdict.value == "deny"
