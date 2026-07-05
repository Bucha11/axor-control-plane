"""Deterministic replay & counterfactuals (spec, section 13).

replay() folds an event sequence into per-step GovernanceState using the SAME
gate/degradation code the runtime uses. A counterfactual passes a modified
config; soundness holds up to the first step where the counterfactual verdict
differs from the recorded one (first-divergence rule). Steps past divergence
are still folded but flagged hypothetical — callers render them as such and
MUST NOT score them.

Adjudicator exception: the adjudicator is the one gate with an LLM inside.
Replay NEVER re-runs it — its recorded verdict is used as-is; if a
counterfactual changes the inputs that reached the adjudicator, that step is
a divergence point by definition. All gates before it are pure.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from axor_kernel.errors import SchemaVersionError
from axor_kernel.events import SCHEMA_VERSION, Event
from axor_kernel.state import GovernanceState


@dataclass(frozen=True)
class ReplayStep:
    event: Event
    state: GovernanceState
    recorded_verdict: str | None
    reevaluated_verdict: str | None
    hypothetical: bool


@dataclass(frozen=True)
class ReplayResult:
    steps: tuple[ReplayStep, ...]
    first_divergence: int | None


def check_schema(events: Sequence[Event]) -> None:
    major = SCHEMA_VERSION.split(".")[0]
    for e in events:
        if e.schema_version.split(".")[0] != major:
            raise SchemaVersionError(
                f"event seq={e.seq}: schema {e.schema_version}, kernel {SCHEMA_VERSION}"
            )


# config object: KernelConfig, lands with gates port
def replay(events: Sequence[Event], config: object | None = None) -> ReplayResult:
    """Fold events; with a non-recorded config this is a counterfactual run."""
    check_schema(events)
    raise NotImplementedError("fold loop lands with the gate pipeline port from axor-core")
