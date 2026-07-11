// Shared E2E helpers. The app is a hash-routed SPA whose funnel position lives
// in a persisted Zustand store (localStorage key "axor-app"). To reach a deep
// surface deterministically — without clicking through onboarding every test —
// we seed that store before the page loads, then deep-link to the tab.
import type { APIRequestContext, Page } from "@playwright/test";
import { expect } from "@playwright/test";

// The Vite dev server proxies /v1 -> backend and /axor -> proxy, so in the
// browser everything is same-origin. For direct backend seeding from the test
// process we talk to the backend port directly.
export const BACKEND = "http://127.0.0.1:8400";

export const SEEDED = { block: "ex_block", pass: "ex_pass" } as const;

// A fresh node id per test run — intervention tests mutate persisted plane state
// (pause, budget cap), so reusing a name across runs against the same DB would
// carry that state over and flip a "Pause" button to "Resume".
export function uniqueNode(prefix: string): string {
  return `${prefix}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

export type Mode = "none" | "demo" | "proxy" | "adapter";

interface ConnOpts {
  mode: Mode;
  testBench?: boolean;
  tools?: { name: string; url: string }[];
  apiToken?: string;
}

// Seed the persisted store BEFORE any script on the page runs. Mirrors zustand
// persist's on-disk shape ({ state, version }); functions come from the store
// initializer on hydrate, so only data fields are needed here.
export async function setConnection(page: Page, opts: ConnOpts): Promise<void> {
  const state = {
    connection: {
      mode: opts.mode,
      tools: opts.tools ?? [],
      testBench: opts.testBench ?? opts.mode === "demo",
    },
    lastRunId: null,
    apiToken: opts.apiToken ?? "",
  };
  await page.addInitScript(
    (s) => window.localStorage.setItem("axor-app", JSON.stringify({ state: s, version: 0 })),
    state,
  );
}

// Deep-link to a hash route (e.g. "replay/ex_block?cursor=3").
export async function goHash(page: Page, hash: string): Promise<void> {
  await page.goto("/#/" + hash.replace(/^#?\/?/, ""));
}

// Ingest the two canned adapter-fidelity runs (recorded verdicts + provenance)
// so Replay, the taint graph and two-sided regression have real data.
export async function seedAdapterRuns(request: APIRequestContext): Promise<void> {
  const r = await request.post(`${BACKEND}/v1/demo/seed-adapter-runs`);
  expect(r.ok(), "seed-adapter-runs should succeed").toBeTruthy();
}

// Make a governed node exist on the plane immediately (a heartbeat upserts its
// reported state), so Control tests don't depend on spawning a real IntentLoop
// and waiting for its first heartbeat.
export async function seedNode(
  request: APIRequestContext,
  nodeId: string,
): Promise<void> {
  const r = await request.post(`${BACKEND}/v1/plane/${nodeId}/telemetry`, {
    data: {
      run_id: `${nodeId}-hb`,
      scenario: "live",
      events: [
        {
          seq: 0,
          kind: "heartbeat",
          payload: { applied_version: 0, level: "NORMAL", budget_remaining: null },
        },
      ],
    },
  });
  expect(r.ok(), "node heartbeat should be accepted").toBeTruthy();
}
