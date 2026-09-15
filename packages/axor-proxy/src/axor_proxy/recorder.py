"""JSONL trace writer. The trace is the portable artifact (architecture, section 1)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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

    async def lines(self) -> list[dict[str, Any]]:
        """The recorded trace, as the kernel-schema lines it holds.

        What the uploader sends and what `axor_eval.audit.from_trace` reads —
        the same artifact, so the cases this proxy reports and the cases the
        control plane derives from the uploaded run are derived from the same
        bytes rather than from two views of them.
        """
        if not await self._path.exists():
            return []
        text = await self._path.read_text(encoding="utf-8")
        return [json.loads(raw) for raw in text.splitlines() if raw.strip()]
