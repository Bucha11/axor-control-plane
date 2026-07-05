"""Desired state (control-plane lattice) and derived governance state."""
from __future__ import annotations

from dataclasses import dataclass, field

from axor_kernel.events import Fact


@dataclass(frozen=True)
class Injection:
    """Single-shot, at-most-once by id (protocol note, section 4)."""

    injection_id: str
    text: str
    reason: str
    operator: str
    sig: str


@dataclass(frozen=True)
class DesiredState:
    """Control-plane target posture for one node. LWW by version.

    Lattice rule: `stopped` is absorbing — merge() keeps later writes in the
    record but they have no effect; the adapter reports them as noop_absorbed.
    `budget_cap_calls` is decrease-only AT THE ADAPTER: the narrowing rule is
    enforced next to the gates, not in backend validation (protocol, section 3).
    """

    version: int
    stopped: bool = False
    paused: bool = False
    budget_cap_calls: int | None = None
    pending_injection: Injection | None = None

    def merge(self, newer: DesiredState) -> DesiredState:
        if newer.version <= self.version:
            return self
        if self.stopped:
            return DesiredState(version=newer.version, stopped=True)
        return newer


@dataclass
class GovernanceState:
    """Everything the scrubber shows per step; folded from events by replay()."""

    level_name: str = "NORMAL"
    tainted_roots: set[str] = field(default_factory=set)
    confidentiality_floor: int = 0     # high-water mark, never lowered by content
    budget_spent_calls: int = 0
    facts: dict[str, Fact] = field(default_factory=dict)
    consumed_injection_ids: set[str] = field(default_factory=set)
