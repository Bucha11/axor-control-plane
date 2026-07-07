# Known limits (measured, honest)

Method: `scripts/load_smoke.py` — N concurrent SSE subscribers on the audit
stream while single-event ingest POSTs arrive from 50 concurrent workers.
Numbers below: single uvicorn worker, **SQLite** (the dev default), 4-core
container, 2026-07.

| Load | Result |
|---|---|
| 50 SSE subscribers + 1500 ingests | 0 errors; every subscriber got every event (50.0× fan-out) |
| Ingest throughput ceiling | **~70 rps** (SQLite write serialization is the bottleneck) |
| Ingest latency at ~70 rps | p50 ≈ 0.4–0.6 s · p95 ≈ 1.3–1.7 s · p99 ≈ 2–2.7 s |

Reading: correctness holds under pressure (no drops, no SSE stalls); raw
ingest throughput is DB-bound. For real deployments use Postgres (the compose
default) — expected substantially higher, **not yet measured**; re-run the
smoke against compose and update this table before quoting numbers.

A single agent emits a few events per second at most, so ~70 rps ≈ dozens of
concurrently chatty agents on the dev database. Fine for evaluation; use
Postgres for a fleet.
