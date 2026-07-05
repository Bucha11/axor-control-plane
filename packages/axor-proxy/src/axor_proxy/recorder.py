"""JSONL trace writer. The trace is the portable artifact (architecture, section 1)."""
from __future__ import annotations

from pathlib import Path

import anyio
from axor_core.kernel.events import Event, event_to_json_line


class TraceRecorder:
    """Append-only writer; one file per run. No raw request/response bodies
    pass through here — the caller records observations, not dumps (spec, section 6)."""

    def __init__(self, trace_dir: Path, run_id: str) -> None:
        trace_dir.mkdir(parents=True, exist_ok=True)
        self.path = trace_dir / f"{run_id}.jsonl"
        self._path = anyio.Path(self.path)

    async def record(self, event: Event) -> None:
        line = event_to_json_line(event) + "\n"
        async with await self._path.open("a", encoding="utf-8") as f:
            await f.write(line)
