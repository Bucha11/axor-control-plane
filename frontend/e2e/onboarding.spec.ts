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
    await expect(page.getByText(/tools: web_search · get_weather/).first()).toBeVisible();
    // A stdio server (command:) goes through the local gateway — the proxy
    // spawns a REAL process (our stdio mock) and handshakes with it.
    await page
      .getByPlaceholder(/mcpServers/)
      .fill('{"mcpServers": {"local_stdio": {"command": "python", "args": ["-m", "axor_proxy.stdio_mock"]}}}');
    await page.getByRole("button", { name: /Discover MCP tools/ }).click();
    await expect(page.getByText(/registered 1 MCP server · 2 tools discovered/)).toBeVisible();
    await expect(page.getByText("local_stdio", { exact: true })).toBeVisible();
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

  test("the adapter path hands over a key and a snippet, not just a label", async ({ page }) => {
    // Choosing `adapter` used to set a client-side flag and nothing else: it
    // unlocked Control and left the user to discover axor-wrap, and — after the
    // plane channel learned to authenticate — an unmentioned ingest key too.
    await goHash(page, "get-started");
    await page.getByRole("button", { name: /Load example tools/ }).click();
    await page.getByRole("button", { name: /Continue/ }).click();
    await page.getByRole("button", { name: /Continue/ }).click();
    await page.getByRole("button", { name: "adapter", exact: true }).click();

    // The snippet is real setup: the wrapped toolset, the connector, the gate.
    const snippet = page.locator("pre", { hasText: "PlaneConnector" });
    await expect(snippet).toBeVisible();
    await expect(snippet).toContainText("pip install 'axor-wrap[plane]'");
    await expect(snippet).toContainText("node.gate(toolset)");
    // …and it names the declared tools, not a placeholder.
    await expect(snippet).toContainText("web_search");
    await expect(snippet).toContainText("<mint a key above>");

    // Minting binds the key to this node id and shows the secret exactly once.
    await page.getByRole("button", { name: /Mint node-bound key/ }).click();
    await expect(page.getByText(/shown once/)).toBeVisible();
    await expect(snippet).not.toContainText("<mint a key above>");
    await expect(snippet).toContainText(/ingest_key="ak_/);

    // The honest boundary is stated where the user chooses, not only in a docstring.
    await expect(page.getByText(/one-shot injection, context excision and replan/)).toBeVisible();
  });
});
