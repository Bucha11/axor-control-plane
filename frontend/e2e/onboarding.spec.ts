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

    // Step 3: the preflight — and the way to the wrap, for refusing calls.
    await expect(page.getByRole("heading", { name: "Check every tool answers through the proxy." })).toBeVisible();
    await page.getByTestId("proxy-to-wrap").click();
    await expect(page.getByTestId("adapter-onboarding")).toBeVisible();
  });

  test("the proxy as a guard: two files, the command, the addresses", async ({ page }) => {
    await goHash(page, "connect?for=production");
    await page.getByTestId("prod-way-proxy").click();
    await page.getByTestId("guard-example").click();
    await expect(page.getByLabel("what send_report does")).toHaveValue("EXPORT");
    const [manifests] = await Promise.all([
      page.waitForEvent("download"), page.getByTestId("guard-save-manifests").click(),
    ]);
    expect(manifests.suggestedFilename()).toBe("axor.manifests.json");
    const bundle = JSON.parse(await (await import("node:fs/promises")).readFile(await manifests.path(), "utf8"));
    const ids = bundle.manifests.map((m: { id: string }) => m.id).sort();
    expect(ids).toEqual(["run_query", "send_report", "web_search"]);
    await expect(page.getByTestId("guard-run"))
      .toContainText("axor-proxy --guard --config tools.json --manifests axor.manifests.json --node-id agent-1");
    await expect(page.getByTestId("guard-urls")).toContainText("http://localhost:8401/t/web_search/");
    await expect(page.getByTestId("guard-live")).toContainText("waiting for agent-1");
  });

  test("wrapping in code: the snippet checks the tools the scanner found", async ({ page }) => {
    await goHash(page, "connect?for=production");
    await page.getByTestId("prod-way-code").click();

    // LangChain is the default: the middleware for control, the wrapped tools for refusing calls.
    const snippet = page.getByTestId("adapter-snippet");
    await expect(snippet).toContainText("pip install 'axor-langchain[plane]' axor-wrap");
    await expect(snippet).toContainText("middleware=[AxorMiddleware()]");
    await expect(snippet).toContainText("wrap_callables(");
    await expect(snippet).toContainText("AXOR_NODE_ID=agent-1");

    // The scanner finds the tools; what each does is a choice, prefilled.
    await page.getByTestId("adapter-files").setInputFiles({
      name: "agent.py", mimeType: "text/x-python", buffer: Buffer.from(AGENT_PY),
    });
    const tools = page.getByTestId("adapter-tools");
    const missing = page.getByText(/scanner is not installed/);
    await expect(tools.or(missing)).toBeVisible({ timeout: 15_000 });
    if (await missing.isVisible()) return;
    await expect(page.getByLabel("what send_email does")).toHaveValue("EXPORT");
    await page.getByTestId("adapter-tool-read_inbox").getByText("brings in outside text").click();
    await page.getByTestId("adapter-download").click();
    await expect(page.getByTestId("adapter-download")).toContainText("saved axor.manifests.json");
    await expect(snippet).toContainText('json.load(open("axor.manifests.json"))');
    await expect(snippet).toContainText('"send_email": send_email');

    // Plain Python: a toolset and the gate, with the same tools.
    await page.getByRole("button", { name: "Plain Python" }).click();
    await expect(snippet).toContainText("WrappedToolset(");
    await expect(snippet).toContainText("node.gate(toolset)");
    // Claude: its own tools run on axor-core — no scan step.
    await page.getByRole("button", { name: /Claude Agent/ }).click();
    await expect(snippet).toContainText("session = axor_claude.make_session()");
    await expect(page.getByTestId("adapter-files")).toHaveCount(0);
  });
});

const AGENT_PY = [
  "from langchain_core.tools import tool",
  "",
  "@tool",
  "def read_inbox(folder: str) -> str:",
  '    """Read the user\'s email inbox."""',
  "    return folder",
  "",
  "@tool",
  "def send_email(to: str, body: str) -> str:",
  '    """Send an email to a recipient."""',
  "    return to",
  "",
].join("\n");
