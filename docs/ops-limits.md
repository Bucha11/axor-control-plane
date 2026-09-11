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

## Reading one run back — the cost is the run's size, not the request's

Replay, containment, the causal subgraph, influence, provenance and the node
coverage panel all go through `traces.events_for`, and every one of them
materialises the **whole** run: the stored lines as strings, then as kernel
`Event` objects. There is no windowing, and there cannot be one — replay is a
fold over the complete trace, which is what makes it reproduce.

Measured on SQLite, one process, events of ~120 bytes:

| Run size | Read from the DB | Parse | Total | Peak RSS |
|---|---|---|---|---|
| 50 000 | 0.58 s | 0.43 s | **1.0 s** | 166 MiB |
| 200 000 | 3.27 s | 2.13 s | **5.4 s** | 407 MiB |

And end to end, with the response built:

| Request | Result |
|---|---|
| `GET /v1/replay/{run}` on 200 000 events | 200 in **9.0 s**, 71 MB body, 545 MiB peak RSS |
| `GET /v1/runs/{run}/provenance?focus=v1&k=2` on the same run | 200 in **5.1 s** to return 40 bytes |

The second row is the shape to remember: `AXOR_MAX_KHOP_K` and
`AXOR_MAX_KHOP_LIMIT` bound the *walk*, not the load underneath it, so a
two-hop question about one value still pays for the whole trace. Both routes
need only `read` scope.

`AXOR_MAX_EVENTS_PER_RUN` (default 250 000) is what keeps this bounded, and it
is enforced on **ingest** — the batch that would cross it gets a 413 naming a
new run id as the remedy. A ceiling on the read would make an already-recorded
trace permanently unreadable, and an audit log that cannot be read is worse than
one that is slow. Lower it if your deployment answers these routes for
interactive users; a real multi-node trace is thousands of events.

## One backend process — a correctness limit, not a tuning knob

**Run exactly one backend replica.** An earlier version of this page suggested
several uvicorn workers behind a load balancer, with SSE subscribers sticking
to a worker. That is wrong, and the failure is silent rather than loud:

- **The event bus is in-process** (`broadcast.py`). A publish on worker A never
  reaches a subscriber on worker B. The audit stream would still open, still
  replay history from the database, and then simply never show a live event —
  a stream that looks healthy and is not.
- **Every worker runs its own background sweeps.** The stale monitor, the EE
  regression scheduler and the entitlement pass are per-process, so N workers
  page the on-call N times for one silent node, fire the corpus N times per
  interval, and send the vendor N renewal requests and the operator N copies of
  each expiry notice.
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
