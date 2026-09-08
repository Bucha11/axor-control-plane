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

from axor_proxy.runs import Run, evidence_to_dict


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
        evidence = [evidence_to_dict(c) for c in run.evidence]
        client = self._client or httpx.AsyncClient(timeout=15.0)
        owns = self._client is None
        h = self._headers
        try:
            # raise_for_status so a backend 4xx (auth off-key, bad payload) is an
            # honest {"uploaded": false}, not a silent success.
            (await client.post(
                f"{self._base}/v1/ingest/{run.run_id}", json=payload, headers=h,
            )).raise_for_status()
            (await client.post(
                f"{self._base}/v1/runs/{run.run_id}/evidence",
                json={"node_id": run.node_id, "evidence": evidence}, headers=h,
            )).raise_for_status()
            # No pin call here. The must-block auto-pin (decision 11) is the
            # backend's, decided in POST /v1/runs/{id}/evidence above: it is the
            # system of record and it can see whether the trace actually
            # recorded a denial for the pin to hold. Pinning from here too put
            # the same policy in two places, and this one could not check.
            return {"uploaded": True, "events": len(events),
                    "evidence": len(evidence)}
        except httpx.HTTPError as exc:
            return {"uploaded": False, "error": type(exc).__name__, "detail": str(exc)}
        finally:
            if owns:
                await client.aclose()
