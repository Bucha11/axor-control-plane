// Global setup: seed the backend once so every spec starts from a known corpus
// (two adapter-fidelity runs). Node/plane state is seeded per-test to keep
// intervention tests isolated.
import { request } from "@playwright/test";
import { BACKEND } from "./helpers";

export default async function globalSetup(): Promise<void> {
  const ctx = await request.newContext();
  // The webServer readiness gate already waited for the backend to serve; still,
  // retry briefly in case seed races the very first request after boot.
  for (let attempt = 0; attempt < 20; attempt++) {
    try {
      const r = await ctx.post(`${BACKEND}/v1/demo/seed-adapter-runs`);
      if (r.ok()) break;
    } catch {
      /* backend not ready yet */
    }
    await new Promise((res) => setTimeout(res, 500));
  }
  await ctx.dispose();
}
