"""Optional trace + evidence upload from the proxy to the backend.

A portable trace is the primary artifact (architecture section 1): written
locally by the proxy, importable into the backend. When `--backend-url` is set,
the proxy pushes the run's JSONL events and its EvidenceCases to the backend on
claim, so the Eval/Replay/Regression surfaces light up without a manual step.
Best-effort: an upload failure never breaks the local run — the trace file on
disk stays the system of record.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anyio
import httpx

from axor_proxy.runs import Run


class BackendUploader:
    def __init__(
        self,
        backend_url: str,
        client: httpx.AsyncClient | None = None,
        ingest_key: str | None = None,
    ) -> None:
        self._base = backend_url.rstrip("/")
        self._client = client
        # A scoped `ingest` API key (architecture section 9). Required when the
        # backend has auth enabled; ignored when it is open.
        self._headers = {"Authorization": f"Bearer {ingest_key}"} if ingest_key else {}

    async def upload(self, run: Run, trace_path: Path) -> dict[str, Any]:
        text = await anyio.Path(trace_path).read_text()
        events = [json.loads(line) for line in text.splitlines() if line.strip()]
        payload = {"node_id": run.node_id, "scenario": run.scenario, "events": events}
        client = self._client or httpx.AsyncClient(timeout=15.0)
        owns = self._client is None
        h = self._headers
        try:
            # raise_for_status so a backend 4xx (auth off-key, bad payload) is an
            # honest {"uploaded": false}, not a silent success.
            stored = (await client.post(
                f"{self._base}/v1/ingest/{run.run_id}", json=payload, headers=h,
            ))
            stored.raise_for_status()
            # No evidence call, and no pin call. Both are the backend's, from
            # the trace this request just delivered: the uploaded events carry
            # the faults and the claim, so the system of record derives the same
            # cases with `axor_eval.audit.from_trace` — the derivation this
            # proxy used for its own answer. Posting them too was a second write
            # of one derivation, and it was the reason the audit ran only where
            # a proxy ran: a path that could not post evidence had none.
            return {"uploaded": True, "events": len(events),
                    "evidence": int(stored.json().get("evidence", 0))}
        except httpx.HTTPError as exc:
            return {"uploaded": False, "error": type(exc).__name__, "detail": str(exc)}
        finally:
            if owns:
                await client.aclose()
