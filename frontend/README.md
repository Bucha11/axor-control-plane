# frontend

React 18 + TS (strict), Vite. Zustand (persisted connection state), TanStack
Query, a hash router (`src/router.ts`), and a hand-rolled SVG taint graph — no
chart lib. Design principle: **quiet-until-wrong**.

Dev: `pnpm i && pnpm dev` → http://localhost:5173 (Vite proxies `/v1`→backend:8400,
`/axor`→proxy:8401). Build: `pnpm build`. Typecheck: `npx tsc --noEmit`.

Layout:

| Path | What |
|---|---|
| `src/App.tsx` | shell: hash router → tab, nav + more-menu, connection badge |
| `src/api.ts` | typed backend/proxy client + SSE stream helpers |
| `src/store.ts` | connection mode (`none/demo/proxy/adapter`), token, last-run |
| `src/tabs/` | Home, Experiment (Eval), Replay, ControlTab, ConfigBuilder, Regression, Health, Settings, Pricing, Onboarding, ExpertView |
| `src/components/` | EvidenceCase, ScenarioDelta, Locked (availability upsell) |
| `src/tabs/TaintGraph.tsx` | provenance graph panel (k-hop, expand-on-click, edge→run) |
| `src/generated/` | TS types from the kernel event schema (`scripts/gen_ts_types.py`) |

Tabs gate by connection depth: Control needs `adapter`; each locked surface shows
what unlocks it. The demo landing (`src/demo/`) is a standalone second bundle at
`/demo.html`.
