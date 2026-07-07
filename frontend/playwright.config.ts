import { defineConfig, devices } from "@playwright/test";

// End-to-end tests for the whole control plane: they drive the real React app
// against a real backend + observe-only proxy, exactly as a browser would.
//
// The three servers are booted by `webServer` below (reused if already up, so
// `pnpm dev` + a running stack makes the suite start instantly). The browser is
// the pre-installed Chromium under PLAYWRIGHT_BROWSERS_PATH.
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
        // The image pre-installs a fixed Chromium build under
        // PLAYWRIGHT_BROWSERS_PATH that may not match the runner's expected
        // revision; point straight at it (override with AXOR_CHROMIUM if needed)
        // so we never try to download a browser.
        launchOptions: {
          executablePath:
            process.env.AXOR_CHROMIUM ||
            "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
        },
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
        AXOR_DATABASE_URL: "sqlite+aiosqlite:////tmp/axor-e2e.db",
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
      env: { AXOR_PROXY_DEMO: "1", AXOR_BACKEND_URL: "http://127.0.0.1:8400" },
    },
    {
      command: "pnpm dev --port 5173 --strictPort",
      url: "http://localhost:5173",
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
