"""Fault injection driven by declarative scenario configs (axor-scenarios)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaultSpec:
    tool: str
    trigger_call_index: int      # inject on the Nth call to `tool`
    fault_type: str              # "timeout" | "error" | "empty" | "corrupt"


def should_inject(spec: FaultSpec, tool: str, call_index: int) -> bool:
    return spec.tool == tool and spec.trigger_call_index == call_index
