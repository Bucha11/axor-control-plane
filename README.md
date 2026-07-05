# axor-control-plane

Runtime governance and evaluation platform for LLM agents. Monorepo.

Naming note: "control plane" is both this platform and one of its subsystems
(spec section 12 — the SSE+POST advisory channel). In code and docs the
subsystem is always called the **plane service** (`axor_backend.plane`);
"control plane" unqualified means the platform.

| Package | What | Rule that shapes it |
|---|---|---|
| `packages/axor-kernel` | **Staging for `axor_core.kernel`**: events schema, desired-state lattice, degradation recompute, replay | Rule 0 = shared code, not a shared package: this merges into axor-core as its pure submodule, then this dir is deleted and imports switch |
| `packages/axor-proxy` | Observe-only tool proxy | Auth passthrough byte-for-byte; two intervention points only (fault, observation) |
| `packages/axor-backend` | FastAPI: ingest, control plane (SSE+POST), replay API, GraphStore (Kùzu) | Backend persists and fans out; it never interprets governance — that's the kernel's |
| `frontend/` | React + TS (Zustand, TanStack Query, Cytoscape) | quiet-until-wrong; TS types generated from kernel Pydantic models |

## Dev

```
uv sync --all-packages                   # workspace install
uv run pytest                            # kernel tests
uv run scripts/gen_ts_types.py           # schema -> frontend/src/generated
cd frontend && pnpm i && pnpm dev
docker compose up                        # self-hosted: proxy + backend + postgres
```

## Ecosystem boundary

Existing PyPI packages are **external dependencies**, never workspace members:

| Package | Role here |
|---|---|
| `axor-core` | enforcement runtime; the platform imports its pure submodule `axor_core.kernel` for replay (purity guarded by a contract test, not packaging) |
| `axor-eval` | scenario catalog + scoring — the proxy interprets its declarative scenario specs, the backend imports its scorers |
| `axor-probe` | health-check verdicts surfaced on the Eval tab |
| `axor-sentinel` | cross-session graph semantics; GraphStore here is its storage face |

Dependency direction is one-way: ecosystem -> never depends on -> platform. Cost accepted: the backend image carries axor-core's full dependency tree.

Specs: `docs/` — UI v0.14 · architecture v0.1 · control-plane protocol v0.2 · monetization v0.1 · implementation plan v0.2. Mockups: `mockups/`.
