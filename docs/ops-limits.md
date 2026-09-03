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
subscriber counts; the next ceiling is single-process SSE fan-out.

A single agent emits a few events per second at most, so ~150 rps ≈ 50–100
concurrently chatty agents on one backend process. SQLite is fine for
evaluation; use Postgres for a fleet.

## One backend process — a correctness limit, not a tuning knob

**Run exactly one backend replica.** An earlier version of this page suggested
several uvicorn workers behind a load balancer, with SSE subscribers sticking
to a worker. That is wrong, and the failure is silent rather than loud:

- **The event bus is in-process** (`broadcast.py`). A publish on worker A never
  reaches a subscriber on worker B. The audit stream would still open, still
  replay history from the database, and then simply never show a live event —
  a stream that looks healthy and is not.
- **Every worker runs its own background sweeps.** The stale monitor and the
  EE regression scheduler are per-process, so N workers page the on-call N
  times for one silent node and fire the corpus N times per interval.
- **Every worker migrates at boot.** `init_db` runs `alembic upgrade head` in
  the app lifespan, so simultaneous starts race on the same schema change.
- **Every worker rehydrates its own taint graph** from the event log — correct,
  but the memory cost is per worker, not shared.

None of this is a throughput ceiling you can raise with hardware; it is what
"single-instance by design" means in the architecture note. Horizontal scaling
needs the bus moved out of the process (Postgres `LISTEN`/`NOTIFY` is the
intended replacement), the sweeps given a leader election, and migrations moved
to a deploy step. Until then, scale up rather than out, and treat >150 rps as
unknown territory to measure in your own deployment.
