import { existsSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { defineConfig, devices } from "@playwright/test";

// This image pre-installs a fixed Chromium build under PLAYWRIGHT_BROWSERS_PATH
// that may not match the runner's expected revision, so point straight at it
// when it exists (override with AXOR_CHROMIUM). On a plain CI runner it is
// absent — fall back to Playwright's own managed browser (`playwright install
// chromium`), i.e. leave executablePath unset.
const PREINSTALLED = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const chromiumPath =
  process.env.AXOR_CHROMIUM || (existsSync(PREINSTALLED) ? PREINSTALLED : undefined);

// End-to-end tests for the whole control plane: they drive the real React app
// against a real backend + observe-only proxy, exactly as a browser would.
//
// The three servers are booted by `webServer` below (reused if already up, so
// `pnpm dev` + a running stack makes the suite start instantly). The browser is
// the pre-installed Chromium under PLAYWRIGHT_BROWSERS_PATH.

// Each run gets its own state directory, and that is a correctness property
// rather than tidiness.
//
// The suite deliberately mints a fresh node id per run (helpers.uniqueNode), so
// that intervention state — a pause, a budget cap — cannot carry into the next
// run. But the NODES themselves persisted in the shared database, the topology
// graph renders every node the org has, and after a handful of local runs they
// crowd enough that one node's circle sits on another's and intercepts its
// click. control-graph then fails on a codebase that is fine.
//
// CI never saw it: a fresh container starts with an empty /tmp. So the failure
// mode was "the suite degrades the more you use it, and only locally" — which
// reads as "my change broke the graph" and costs an hour before anyone suspects
// leftover rows. Per-run paths make a local run mean the same thing as a CI run.
//
// Note the interaction with `reuseExistingServer` below: when a stack is
// already up (a dev running `pnpm dev`), Playwright reuses it and never applies
// this env at all — that run keeps whatever database the running server opened.
// Stashed in the environment, not just a const: Playwright evaluates this
// config again in each worker process, and a per-process id would mint a second
// directory there that nothing ever writes to. Workers are forked after this
// runs, so they inherit the id and agree on one directory per run.
process.env.AXOR_E2E_RUN ??=
  `${Date.now().toString(36)}-${process.pid.toString(36)}`;
const RUN_DIR = `${tmpdir()}/axor-e2e-${process.env.AXOR_E2E_RUN}`;
const RUN_DB = `${RUN_DIR}/backend.db`;
mkdirSync(RUN_DIR, { recursive: true });

// Sweep state from earlier runs. Age-gated rather than "everything but mine",
// because two suites can legitimately run at once (two terminals, two branches)
// and deleting a database another run has open would break it for no reason.
const STALE_AFTER_MS = 6 * 60 * 60 * 1000;
for (const entry of readdirSync(tmpdir())) {
  if (!entry.startsWith("axor-e2e-")) continue;
  const path = `${tmpdir()}/${entry}`;
  try {
    if (Date.now() - statSync(path).mtimeMs > STALE_AFTER_MS) {
      rmSync(path, { recursive: true, force: true });
    }
  } catch {
    /* raced with another run's own sweep — nothing to do */
  }
}

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false, // interventions mutate shared plane state; keep ordered
  workers: 1,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  use: {
    baseURL: "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: chromiumPath ? { executablePath: chromiumPath } : {},
      },
    },
  ],
  webServer: [
    {
      // FastAPI system-of-record + plane service.
      command:
        "uv run --no-sync uvicorn axor_backend.main:app --factory --host 127.0.0.1 --port 8400",
      cwd: "..",
      url: "http://127.0.0.1:8400/v1/auth/status",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        AXOR_DATABASE_URL: `sqlite+aiosqlite:///${RUN_DB}`,
        AXOR_ALLOW_UNSIGNED: "1",
      },
    },
    {
      // Observe-only proxy in demo-mode (mock tools + scripted agent + governed
      // spawn), auto-uploading to the backend.
      command: "uv run --no-sync axor-proxy --demo --host 127.0.0.1 --port 8401",
      cwd: "..",
      url: "http://127.0.0.1:8401/axor/healthz",
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        AXOR_PROXY_DEMO: "1",
        AXOR_BACKEND_URL: "http://127.0.0.1:8400",
        // Same reasoning as RUN_DB: the run's traces are the run's, not a pile
        // that grows in the repo checkout.
        AXOR_TRACE_DIR: `${RUN_DIR}/traces`,
      },
    },
    {
      command: "pnpm dev --port 5173 --strictPort",
      url: "http://localhost:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
