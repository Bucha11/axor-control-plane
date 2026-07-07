"""Streaming passthrough (launch-readiness §1): a chunked/SSE upstream body
flows through the proxy without buffering, and the observation records the
size + sha256 accumulated over the stream."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from axor_proxy.app import ProxyState, create_app
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

CHUNKS = [b"data: one\n\n", b"data: two\n\n", b"data: [DONE]\n\n"]


def streaming_upstream() -> Starlette:
    async def handler(request: Request) -> StreamingResponse:
        async def gen():  # noqa: ANN202
            for chunk in CHUNKS:
                yield chunk

        return StreamingResponse(gen(), media_type="text/event-stream")

    return Starlette(routes=[Route("/{path:path}", handler, methods=["GET", "POST"])])


@pytest.fixture
def proxy(tmp_path: Path) -> httpx.AsyncClient:
    upstream_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=streaming_upstream()),
        base_url="http://upstream.test",
    )
    state = ProxyState(
        tools={"llm_tool": "http://upstream.test/stream"},
        trace_dir=tmp_path,
        client=upstream_client,
    )
    app = create_app(state)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )
    client.proxy_state = state  # type: ignore[attr-defined]
    return client


async def test_sse_body_streams_through_chunk_by_chunk(proxy: httpx.AsyncClient) -> None:
    await proxy.post("/axor/runs", json={"scenario": "s", "faults": []})

    received: list[bytes] = []
    async with proxy.stream("GET", "/t/llm_tool/") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        # No content-length: the body is chunked through, not buffered.
        assert "content-length" not in resp.headers
        async for chunk in resp.aiter_raw():
            received.append(chunk)

    assert b"".join(received) == b"".join(CHUNKS)


async def test_observation_records_streamed_size_and_hash(
    proxy: httpx.AsyncClient,
) -> None:
    run = (await proxy.post("/axor/runs", json={"scenario": "s", "faults": []})).json()
    resp = await proxy.get("/t/llm_tool/")
    assert resp.content == b"".join(CHUNKS)

    state = proxy.proxy_state  # type: ignore[attr-defined]
    trace = state.runs.get(run["run_id"]).recorder.path.read_text()
    lines = [json.loads(line) for line in trace.splitlines()]
    result = next(ln for ln in lines if ln["kind"] == "tool_result")
    body = b"".join(CHUNKS)
    assert result["payload"]["streamed"] is True
    assert result["payload"]["response_bytes"] == len(body)
    assert result["payload"]["response_sha256"] == hashlib.sha256(body).hexdigest()
    assert result["payload"]["status"] == 200
