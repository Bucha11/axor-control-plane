// Get started — the proxy onboarding (Onboarding.tsx): declare tools → point the
// agent at the proxied URLs → prove the plumbing. The depth toggle sells the
// adapter's extra surfaces honestly.
import { expect, test } from "@playwright/test";
import { goHash } from "./helpers";

test.describe("onboarding", () => {
  test("pasting an MCP config discovers and registers the server's tools", async ({ page }) => {
    await goHash(page, "get-started");
    // The built-in mock MCP server — a real handshake end-to-end, zero creds.
    await page
      .getByPlaceholder(/mcpServers/)
      .fill('{"mcpServers": {"demo_mcp": {"url": "http://127.0.0.1:8401/mock/mcp"}}}');
    await page.getByRole("button", { name: /Discover MCP tools/ }).click();
    await expect(page.getByText(/registered 1 MCP server · 2 tools discovered/)).toBeVisible();
    // The server lands in the declared-tools list with its tool inventory.
    await expect(page.getByText("demo_mcp", { exact: true })).toBeVisible();
    await expect(page.getByText(/tools: web_search · get_weather/)).toBeVisible();
    // stdio-only config is refused honestly.
    await page.getByPlaceholder(/mcpServers/).fill('{"mcpServers": {"local": {"command": "npx"}}}');
    await page.getByRole("button", { name: /Discover MCP tools/ }).click();
    await expect(page.getByText(/stdio needs a local gateway/)).toBeVisible();
  });

  test("walks tools → point agent → connection check", async ({ page }) => {
    await goHash(page, "get-started");
    await expect(page.getByRole("heading", { name: "What tools does your agent use?" })).toBeVisible();

    // Step 1: load the example tool set.
    await page.getByRole("button", { name: /Load example tools/ }).click();
    for (const t of ["web_search", "send_report", "run_query"]) {
      await expect(page.getByText(t, { exact: true }).first()).toBeVisible();
    }
    await page.getByRole("button", { name: /Continue/ }).click();

    // Step 2: the proxied base URLs are shown, ready to copy.
    await expect(page.getByRole("heading", { name: "Point your agent at the proxy." })).toBeVisible();
    await expect(page.getByText("http://127.0.0.1:8401/t/web_search/")).toBeVisible();
    await page.getByRole("button", { name: /Continue/ }).click();

    // Step 3: the preflight, plus the depth choice and its honest upsell copy.
    await expect(page.getByRole("heading", { name: "Prove the plumbing before it matters." })).toBeVisible();
    await page.getByRole("button", { name: "adapter", exact: true }).click();
    await expect(page.getByText("unlocks Control, taint graph, probe health")).toBeVisible();
  });
});
