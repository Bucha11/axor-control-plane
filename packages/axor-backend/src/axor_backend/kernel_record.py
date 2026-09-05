"""Recorded event → kernel objects, so the KERNEL decides.

Rule 0 says the platform imports the gates and never reimplements them, and
until now this platform did reimplement one: the export converter carried a
hand-written mirror of the Lab's reference taint floor. It reimplemented it
because the obvious reading is that the real gate needs content a recorded
trace does not carry.

It does not. `ToolCallGovernor` needs content because it is the LEDGER — the
part that derives a value's provenance as a session runs. The gate itself is a
pure predicate:

    taint_gate(tool, NormalizedIntent, CausalRoot, floor_active, egress_sinks)

and `CausalRoot` is `{sources, sensitive}` with `is_tainted = bool(sources)`.
The kernel writes exactly that into every recorded call (`driving_root`), along
with the structural projection it gated on (`normalized`) and whether the
confidentiality floor was up. By the time an event is written, the ledger's
answer is in it.

So this module is only a DECODER: recorded JSON in, kernel objects out. It
contains no governance logic — every verdict comes from the imported gate.

(The kernel carries the same decoder as `axor_core.policy.from_record`, which is
its long-term home; this platform pins axor-core from PyPI and cannot import it
until a release ships. When one does, this module becomes a re-export.)
"""
from __future__ import annotations

from typing import Any

from axor_core.contracts.anomaly import NormalizedIntent
from axor_core.contracts.taint import TaintSource
from axor_core.taint.causal_root import CausalRoot


class IncompleteRecord(ValueError):
    """The recorded event does not carry what a verdict depends on."""

    def __init__(self, missing: list[str], where: str = "") -> None:
        self.missing = list(missing)
        at = f" at {where}" if where else ""
        super().__init__(
            f"the recorded intent{at} is missing {sorted(missing)} — these decide "
            f"a taint verdict, and absent is not False"
        )


# The fields `taint_gate` reads off a NormalizedIntent. Absent, each could turn a
# recorded DENY into a recomputed ALLOW, so each is required rather than defaulted.
DECISIVE_NORMALIZED_FIELDS = (
    "destination_kind",
    "writes_outside_workdir",
    "executes_generated_code",
)

# Everything else NormalizedIntent declares. The gate does not read these, so a
# record may omit them; they take the neutral value a structural projection has
# when nothing was observed.
_NEUTRAL: dict[str, Any] = {
    "operation": "other",
    "target_kind": "workdir",
    "provenance": "unknown",
    "reads_secret_like_data": False,
    "after_external_read": False,
    "after_secret_access": False,
    "data_flow": "none",
}


def normalized_from_record(
    tool: str, normalized: dict[str, Any] | None, *, where: str = ""
) -> NormalizedIntent:
    """Rebuild the structural projection a recorded call was gated on."""
    record = dict(normalized or {})
    missing = [f for f in DECISIVE_NORMALIZED_FIELDS if f not in record]
    if missing:
        raise IncompleteRecord(missing, where)
    fields = {**_NEUTRAL, **record}
    return NormalizedIntent(
        tool=tool,
        operation=str(fields["operation"]),
        target_kind=str(fields["target_kind"]),
        destination_kind=str(fields["destination_kind"]),
        provenance=str(fields["provenance"]),
        reads_secret_like_data=bool(fields["reads_secret_like_data"]),
        writes_outside_workdir=bool(fields["writes_outside_workdir"]),
        executes_generated_code=bool(fields["executes_generated_code"]),
        after_external_read=bool(fields["after_external_read"]),
        after_secret_access=bool(fields["after_secret_access"]),
        data_flow=str(fields["data_flow"]),
    )


def causal_root_from_record(root: dict[str, Any] | None) -> CausalRoot:
    """Rebuild a value's provenance from its recorded root.

    An unrecognised source name becomes UNKNOWN_EXTERNAL rather than being
    dropped: forgetting a source is the direction that turns tainted into
    trusted, and over-tainting is the safe one — the same direction the causal
    root algebra takes when it unions its inputs.
    """
    record = dict(root or {})
    sources = set()
    for name in record.get("sources") or ():
        try:
            sources.add(TaintSource(str(name)))
        except ValueError:
            sources.add(TaintSource.UNKNOWN_EXTERNAL)
    return CausalRoot(
        sources=frozenset(sources), sensitive=bool(record.get("sensitive"))
    )
