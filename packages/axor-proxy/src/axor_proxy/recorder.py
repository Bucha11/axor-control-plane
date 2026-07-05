"""JSONL trace writer. The trace is the portable artifact (architecture, section 1)."""
from __future__ import annotations

from pathlib import Path

import anyio
from axor_kernel.events import Event


class TraceRecorder:
    """Append-only writer; one file per run. No raw request/response bodies
    pass through here — the caller records observations, not dumps (spec, section 6)."""

    def __init__(self, trace_dir: Path, run_id: str) -> None:
        self._path = anyio.Path(trace_dir / f"{run_id}.jsonl")

    async def record(self, event: Event) -> None:
        line = event.model_dump_json() + "\n"
        async with await self._path.open("a", encoding="utf-8") as f:
            await f.write(line)
