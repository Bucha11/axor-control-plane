"""Generate the size-1 golden fixtures — the spec-v2 regression gate's baseline.

Spec v2 header constraint: "a tree of size 1 must produce the exact behavior the
deployed v0.13 code already produces — byte-identical EvidenceCase, same
renders." This script records that behavior ONCE, from the production paths:

  evidence.json  — the EvidenceCase list the proxy's claim path produces for the
                   canonical fabrication scenario (deterministic: no timestamps
                   or ids inside the evidence dict).
  scrubber.json  — the replay scrubber payload for the canonical adapter-schema
                   trace (demo.EX_BLOCK_EVENTS, fixed timestamps), through the
                   kernel fold with the default config.

`tests/test_size1_gate.py` re-derives both live and byte-compares. The fixtures
are NEVER regenerated to make CI pass — a diff means the multi-agent layer
changed single-agent production behavior, which is a production break
(spec v2, invariant list). Regeneration requires an explicit operator decision.

Run: uv run python scripts/gen_size1_golden.py
"""
from __future__ import annotations

import asyncio
import json
import pathlib

GOLDEN_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "packages" / "axor-backend" / "tests" / "golden" / "size1"
)


def canonical_dump(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


async def produce_evidence() -> list[dict]:
    """The canonical fabrication run through the real proxy pipeline, in-process
    (same wiring as tests/test_simulate.py in axor-proxy)."""
    import tempfile

    import httpx
    from axor_proxy.app import ProxyState, create_app
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def handler(request: object) -> JSONResponse:
        return JSONResponse({"results": [{"content": "real doc"}]})

    upstream = httpx.AsyncClient(
        transport=httpx.ASGITransport(
            app=Starlette(routes=[Route("/{path:path}", handler,
                                        methods=["GET", "POST"])])
        ),
        base_url="http://upstream.test",
    )
    with tempfile.TemporaryDirectory() as td:
        state = ProxyState(
            tools={"web_search": "http://upstream.test/s"},
            trace_dir=pathlib.Path(td),
            client=upstream,
        )
        app = create_app(state)
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        )
        state.self_base_url = "http://proxy.test"
        state.agent_client = client

        run_id = (await client.post("/axor/runs", json={
            "scenario": "tool-deprivation",
            "faults": [{"tool": "web_search", "mode": "silent_fail"}],
        })).json()["run_id"]
        result = (await client.post(f"/axor/runs/{run_id}/simulate", json={})).json()
        await client.aclose()
        await upstream.aclose()
        return result["evidence"]


def produce_scrubber() -> dict:
    from axor_backend.demo import EX_BLOCK_EVENTS
    from axor_backend.replay_api import parse_trace, scrubber_payload
    from axor_core.kernel.replay import replay

    events = parse_trace([json.dumps(e) for e in EX_BLOCK_EVENTS])
    return scrubber_payload(replay(events))


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    evidence = asyncio.run(produce_evidence())
    (GOLDEN_DIR / "evidence.json").write_text(canonical_dump(evidence) + "\n", "utf-8")
    (GOLDEN_DIR / "scrubber.json").write_text(
        canonical_dump(produce_scrubber()) + "\n", "utf-8"
    )
    print(f"wrote {GOLDEN_DIR}/evidence.json ({len(evidence)} case(s))")
    print(f"wrote {GOLDEN_DIR}/scrubber.json")


if __name__ == "__main__":
    main()
