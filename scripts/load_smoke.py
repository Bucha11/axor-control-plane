"""Load smoke (launch-readiness §6): find the first ceiling and write it down.

Not a benchmark — a smoke: N concurrent SSE subscribers on the audit stream
while ingest POSTs arrive at a target rate. Reports achieved rps, ingest
latency percentiles, error count, and whether the SSE side kept delivering.

  uv run scripts/load_smoke.py --base-url http://127.0.0.1:8400 \
      --sse 50 --rps 100 --seconds 15
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import time

import httpx


async def sse_subscriber(client: httpx.AsyncClient, run_id: str, stop: asyncio.Event,
                         counters: dict) -> None:
    try:
        async with client.stream(
            "GET", f"/v1/runs/{run_id}/stream", timeout=httpx.Timeout(5, read=None),
        ) as resp:
            counters["sse_connected"] += 1
            async for line in resp.aiter_lines():
                if stop.is_set():
                    return
                if line.startswith("data:"):
                    counters["sse_events"] += 1
    except Exception:
        counters["sse_errors"] += 1


async def ingest_worker(client: httpx.AsyncClient, run_id: str, seq_start: int,
                        n: int, latencies: list[float], counters: dict) -> None:
    for i in range(n):
        ev = {"schema_version": "1.0", "seq": seq_start + i, "node_id": "load",
              "kind": "tool_call", "ts": "t", "causal_root": None, "gate": None,
              "verdict": "pass", "payload": {"tool": "load_tool", "arg_refs": {}}}
        t0 = time.perf_counter()
        try:
            r = await client.post(f"/v1/ingest/{run_id}",
                                  json={"node_id": "load", "events": [ev]})
            if r.status_code == 202:
                latencies.append(time.perf_counter() - t0)
            else:
                counters["ingest_errors"] += 1
        except Exception:
            counters["ingest_errors"] += 1


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8400")
    ap.add_argument("--sse", type=int, default=50)
    ap.add_argument("--rps", type=int, default=100)
    ap.add_argument("--seconds", type=int, default=15)
    args = ap.parse_args()

    run_id = f"load_{int(time.time())}"
    total = args.rps * args.seconds
    workers = min(args.rps, 50)  # concurrency, not rate-limiting: a smoke
    per_worker = total // workers

    counters = {"sse_connected": 0, "sse_events": 0, "sse_errors": 0,
                "ingest_errors": 0}
    latencies: list[float] = []
    stop = asyncio.Event()

    limits = httpx.Limits(max_connections=args.sse + workers + 10)
    async with httpx.AsyncClient(base_url=args.base_url, limits=limits) as client:
        await client.post(f"/v1/ingest/{run_id}", json={"node_id": "load", "events": []})
        subs = [asyncio.create_task(sse_subscriber(client, run_id, stop, counters))
                for _ in range(args.sse)]
        await asyncio.sleep(1.0)  # let subscribers attach

        t0 = time.perf_counter()
        await asyncio.gather(*[
            ingest_worker(client, run_id, w * per_worker, per_worker,
                          latencies, counters)
            for w in range(workers)
        ])
        elapsed = time.perf_counter() - t0
        await asyncio.sleep(1.0)  # drain SSE
        stop.set()
        for s in subs:
            s.cancel()

    ok = len(latencies)
    lat_sorted = sorted(latencies)
    pct = lambda p: lat_sorted[int(len(lat_sorted) * p)] * 1000 if lat_sorted else 0  # noqa: E731
    print(f"target: {args.rps} rps × {args.seconds}s = {total} events, "
          f"{args.sse} SSE subscribers")
    print(f"ingested OK: {ok}/{total} in {elapsed:.1f}s "
          f"→ achieved {ok / elapsed:.0f} rps")
    print(f"ingest latency ms: p50={pct(0.50):.0f} p95={pct(0.95):.0f} "
          f"p99={pct(0.99):.0f} max={lat_sorted[-1] * 1000 if lat_sorted else 0:.0f}")
    print(f"errors: ingest={counters['ingest_errors']} sse={counters['sse_errors']}")
    print(f"sse: {counters['sse_connected']}/{args.sse} connected, "
          f"{counters['sse_events']} events delivered "
          f"(fan-out ≈ {counters['sse_events'] / max(ok, 1):.1f}× per ingest)")
    if statistics.median(lat_sorted or [0]) > 0.5:
        print("VERDICT: median ingest latency > 500ms — below target; investigate")


if __name__ == "__main__":
    asyncio.run(main())
