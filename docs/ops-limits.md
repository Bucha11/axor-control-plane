# Known limits (measured, honest)

Method: `scripts/load_smoke.py` — N concurrent SSE subscribers on the audit
stream while single-event ingest POSTs arrive from 50 concurrent workers.
Numbers below: single uvicorn worker, 4-core container, 2026-07.

## SQLite (dev default)

| Load | Result |
|---|---|
| 50 SSE subscribers + 1500 ingests | 0 errors; every subscriber got every event (50.0× fan-out) |
| Ingest throughput ceiling | **~70 rps** (SQLite write serialization is the bottleneck) |
| Ingest latency at ~70 rps | p50 ≈ 0.4–0.6 s · p95 ≈ 1.3–1.7 s · p99 ≈ 2–2.7 s |

## Postgres 16 (the compose default)

| Load | Result |
|---|---|
| 50 SSE subscribers + 100 rps × 15 s | 0 errors; 1500/1500 ingested at 102 rps; perfect 50.0× fan-out; p50 ≈ 0.3 s |
| Ingest ceiling, no subscribers | **~150 rps** (≈2× SQLite) |
| Ingest latency at ~150 rps | p50 ≈ 0.27 s · p95 ≈ 0.7 s · p99 ≈ 1.0 s |
| 10 SSE subscribers + 2000 ingests | 159 rps, 0 errors, perfect 10.0× fan-out |
| 50 SSE subscribers pushed at 300 rps | no errors, but sustained rate drops to ~90 rps — **SSE fan-out (50 copies per event on one event loop) becomes the co-bottleneck**, not the DB |

Reading: correctness holds under pressure in every run (zero drops, zero
errors, no SSE stalls). On Postgres the DB stops being the limit at moderate
subscriber counts; the next ceiling is single-process SSE fan-out. If you need
more: run several uvicorn workers behind a load balancer (ingest scales; SSE
subscribers stick to a worker) — unmeasured, so treat >150 rps as unknown
territory and measure your own deployment.

A single agent emits a few events per second at most, so ~150 rps ≈ 50–100
concurrently chatty agents on one backend process. SQLite is fine for
evaluation; use Postgres for a fleet.
