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

Adoption layer (opt-in, so the default stays quiet-until-wrong):
`components/Tooltip.tsx` is a themed hover/focus tooltip on the non-obvious
actions; `components/Coach.tsx` renders a per-surface explainer note that appears
only when **Learn mode** is on (the graduation-cap toggle in the header). Every
tab carries one — Eval, Control, Replay, Regression, Config Builder, Onboarding,
Health, Settings, Expert, Pricing. Learn state lives in the store
(`learnMode` / `learnSeen` / `coachDismissed`, persisted); a one-time Home nudge
offers to turn it on, and Settings → LEARN MODE has the toggle + "reset tips".

## E2E (Playwright)

`pnpm e2e` — drives the real app against a real backend + observe-only proxy.
The config (`playwright.config.ts`) boots all three servers itself (backend,
proxy, Vite) and reuses them if already running, so with a live stack the suite
starts instantly. Chromium is the image's pre-installed build (override the path
with `AXOR_CHROMIUM`). `pnpm e2e:report` opens the last HTML report.

`e2e/` covers every surface as a user story: shell/deep-links, Home funnel,
onboarding, an Eval run that catches the fabrication as an EvidenceCase, Replay
(tool-chip timeline, counterfactual first-divergence, provenance graph, corpus
pinning), Control (locked upsell → live node → pause / decrease-only budget cap /
intervention menu, plus a real end-to-end governed-node spawn), two-sided
Regression, the Config Builder, and Settings. Helpers seed the store's connection
mode via `localStorage` and seed backend/plane state over HTTP, so deep surfaces
are reached deterministically.
