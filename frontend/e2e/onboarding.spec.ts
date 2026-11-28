// Get started — the proxy onboarding (Onboarding.tsx): declare tools → point the
// agent at the proxied URLs → prove the plumbing. The depth toggle sells the
// adapter's extra surfaces honestly.
import { expect, test } from "@playwright/test";
import { goHash, resetPolicy } from "./helpers";

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

  test("a server that cannot be reached is said in words, and the others are still added (QA-486)", async ({ page }) => {
    await page.route("**/axor/mcp/discover", async (r) => {
      const body = r.request().postDataJSON() as { url?: string; name?: string };
      if (body.url) {
        return r.fulfill({ status: 502, json: { error: "mcp_discovery_failed",
          detail: "server unreachable: [Errno -2] Name or service not known" } });
      }
      return r.fulfill({ json: { registered: body.name ?? "fs", proxied_base: "/t/fs/", server: "fs",
        protocol_version: "2025-03-26", transport: "stdio", tools: [{ name: "read_file", description: "" }] } });
    });
    await goHash(page, "get-started");
    await page.getByPlaceholder(/mcpServers/).fill(JSON.stringify({ mcpServers: {
      docs: { url: "https://mcp.nowhere.invalid/sse" }, fs: { command: "npx", args: ["-y", "server-fs"] } } }));
    await page.getByRole("button", { name: /Discover MCP tools/ }).click();
    const note = page.getByText(/registered 1 MCP server · 1 tools discovered/);
    await expect(note).toBeVisible();
    await expect(note).toContainText("docs: could not reach https://mcp.nowhere.invalid/sse");
    await expect(page.getByText(/Errno/)).toHaveCount(0);
    await expect(page.getByText("fs", { exact: true })).toBeVisible();
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

    // Step 2: how to start the proxy with these tools (it can only forward to
    // tools it was started with), then the proxied base URLs, ready to copy.
    await expect(page.getByRole("heading", { name: /Start the proxy with your tools/ })).toBeVisible();
    await expect(page.getByTestId("test-run-start-proxy")).toContainText("tools.json");
    await expect(page.getByText(/axor-proxy --config tools.json --backend-url/)).toBeVisible();
    await expect(page.getByText("http://127.0.0.1:8401/t/web_search/")).toBeVisible();
    await page.getByRole("button", { name: /Continue/ }).click();

    // Step 3: the preflight — and the way to the wrap, for refusing calls.
    await expect(page.getByRole("heading", { name: "Check every tool answers through the proxy." })).toBeVisible();
    await page.getByTestId("proxy-to-wrap").click();
    await expect(page.getByTestId("adapter-onboarding")).toBeVisible();
  });

  test("the proxy as a guard: two files, the command, the addresses", async ({ page, request }) => {
    await resetPolicy(request);
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
    // another port: the command and the addresses follow it
    await page.getByLabel("proxy port").fill("8411");
    await expect(page.getByTestId("guard-run")).toContainText("--node-id agent-1 --port 8411");
    await expect(page.getByTestId("guard-urls")).toContainText("http://localhost:8411/t/web_search/");
  });

  // QA-015: a long name widened the page past its copy button, a name went
  // into the address unencoded, a duplicate vanished, and "not a url" was taken.
  const fitsTheWindow = (page: import("@playwright/test").Page) =>
    page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

  test("the guard's tools: odd names are encoded and wrap, a duplicate or a non-URL is refused (QA-015)", async ({ page, request }) => {
    // the tools land in the workspace's policy, which other specs read
    await resetPolicy(request);
    await goHash(page, "connect?for=production");
    await page.getByTestId("prod-way-proxy").click();
    const name = page.getByPlaceholder("tool name ⏎");
    const url = page.getByPlaceholder("https://where-it-lives ⏎");
    const add = async (n: string, u: string) => { await name.fill(n); await url.fill(u); await url.press("Enter"); };
    await add("web search", "https://a.example/search");
    await expect(page.getByTestId("guard-urls")).toContainText("http://localhost:8401/t/web%20search/");
    await add("web search", "https://b.example/search");
    await expect(page.getByTestId("guard-add-error")).toContainText("web search is already listed");
    await expect(page.getByTestId("guard-tool-web search")).toContainText("https://a.example/search");
    await add("nourl", "not a url");
    await expect(page.getByTestId("guard-add-error")).toContainText("not an http:// or https:// address");
    await expect(page.getByTestId("guard-tool-nourl")).toHaveCount(0);
    // QA-050: a name is at most 64 characters, as a plain id; the address may be long
    await add("A".repeat(3000), "https://b.example");
    await expect(page.getByTestId("guard-add-error")).toContainText("a tool name is at most 64 characters (this one has 3000)");
    await add("A".repeat(64), "https://" + "b".repeat(3000) + ".example");
    await expect(page.getByTestId("guard-add-error")).toHaveCount(0);
    await expect(page.getByTestId("guard-urls")).toContainText("AAAA");
    expect(await fitsTheWindow(page)).toBe(true);
    const copy = await page.getByTestId("guard-urls").locator("xpath=..").getByRole("button", { name: "copy" }).boundingBox();
    expect(copy!.x + copy!.width).toBeLessThanOrEqual(page.viewportSize()!.width);
    await resetPolicy(request);
  });

  test("a found tool's address: the same http(s) rule as the add row (QA-015)", async ({ page, request }) => {
    await resetPolicy(request);
    // a tool the scanner found comes without an address
    await page.addInitScript(() => {
      const raw = JSON.parse(localStorage.getItem("axor-app") ?? '{"state":{},"version":0}');
      raw.state.toolPolicy = { found_tool: { klass: "read", outside: false } };
      localStorage.setItem("axor-app", JSON.stringify(raw));
    });
    await goHash(page, "connect?for=production");
    await page.getByTestId("prod-way-proxy").click();
    const where = page.getByLabel("where found_tool lives");
    await where.fill("not a url");
    await where.press("Enter");
    await expect(page.getByTestId("guard-tool-url-error-found_tool")).toContainText("not an http:// or https:// address");
    await expect(where).toBeVisible();
    await where.fill("https://found.example/api");
    await where.press("Enter");
    await expect(page.getByTestId("guard-tool-url-error-found_tool")).toHaveCount(0);
    await expect(page.getByTestId("guard-tool-found_tool")).toContainText("https://found.example/api");
    await resetPolicy(request);
  });

  test("a test run's tools: the same checks, the same encoding (QA-015)", async ({ page, request }) => {
    await resetPolicy(request);
    await goHash(page, "get-started");
    const name = page.getByPlaceholder("tool name ⏎");
    const url = page.getByPlaceholder("https://endpoint ⏎");
    const add = async (n: string, u: string) => { await name.fill(n); await url.fill(u); await url.press("Enter"); };
    await add("web search", "https://a.example/search");
    await add("web search", "https://b.example/search");
    await expect(page.getByTestId("proxy-add-error")).toContainText("web search is already listed");
    await add("nourl", "ftp://files.example");
    await expect(page.getByTestId("proxy-add-error")).toContainText("not an http:// or https:// address");
    await add("L".repeat(3000), "https://c.example");
    await expect(page.getByTestId("proxy-add-error")).toContainText("a tool name is at most 64 characters");
    await add("L".repeat(64), "https://c.example");
    expect(await fitsTheWindow(page)).toBe(true);
    await page.getByRole("button", { name: /Continue/ }).click();
    await expect(page.getByTestId("proxied-web search")).toHaveText("http://127.0.0.1:8401/t/web%20search/");
    expect(await fitsTheWindow(page)).toBe(true);
    await resetPolicy(request);
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
    await expect(snippet).toContainText("node.gate(WrappedToolset(");
    // each run reaches the plane with its answer, so a claimed success that failed is caught
    await expect(snippet).toContainText("task=request");
    await expect(snippet).toContainText("node.post_ingest(toolset.run_lines(answer)");
    // a synchronous agent: the node runs in the background, its calls go through submit()
    await expect(snippet).toContainText(".start()   # live, in the background");
    await expect(snippet).toContainText("node.submit(node.post_health_check(body))");
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

test.describe("one record of what the tools do", () => {
  test("the guard finds the tools in your code and asks where each lives", async ({ page, request }) => {
    // the workspace's tool policy is shared by every spec: start from an empty one
    await resetPolicy(request);
    await goHash(page, "connect?for=production");
    await page.getByTestId("prod-way-proxy").click();
    await page.getByTestId("guard-scan").setInputFiles({
      name: "agent.py", mimeType: "text/x-python", buffer: Buffer.from(AGENT_PY),
    });
    const row = page.getByTestId("guard-tool-send_email");
    const missing = page.getByText(/scanner is not installed/);
    await expect(row.or(missing)).toBeVisible({ timeout: 15_000 });
    if (await missing.isVisible()) return;
    await expect(page.getByLabel("what send_email does")).toHaveValue("EXPORT");
    await expect(page.getByText(/found the tools, not where they live/)).toBeVisible();
    await page.getByLabel("where send_email lives").fill("https://mail.example/send");
    await page.getByLabel("where send_email lives").press("Enter");
    await expect(row).toContainText("https://mail.example/send");
    // the same record shows in Policy
    await goHash(page, "policy");
    await expect(page.getByTestId("policy-from-connect")).toContainText("send_email");
  });

  test("a test run on your own agent is armed with it, and says what policy would refuse", async ({ page, request }) => {
    await goHash(page, "connect?for=production&how=test");
    await page.getByText("use our mock tools (zero creds)").click();
    await expect(page.getByLabel("what web_search does")).toHaveValue("READ");
    await page.getByRole("button", { name: /Continue/ }).click();
    await page.getByRole("button", { name: /Continue/ }).click();
    await page.getByRole("button", { name: /Test connections/ }).click();
    await page.getByRole("button", { name: /Run a first test/ }).click();
    await expect(page.getByTestId("test-policy-note")).toContainText("knows what 2 of its tools do");
    // arm it, then play the agent through the proxy (the scripted one)
    const [armed] = await Promise.all([
      page.waitForResponse((r) => r.url().endsWith("/axor/runs") && r.request().method() === "POST"),
      page.getByRole("button", { name: /Start the test/ }).click(),
    ]);
    expect(armed.request().postDataJSON().manifests?.length).toBe(2);
    const { run_id } = await armed.json();
    const sim = await request.post(`http://127.0.0.1:8401/axor/runs/${run_id}/simulate`, { data: {} });
    expect(sim.status()).toBe(200);
    await expect(page.getByTestId("would-refuse")).toBeVisible({ timeout: 20_000 });
    // it already was their own agent: the next step is guarding it, not testing one
    await expect(page.getByTestId("catch-guard")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("catch-own")).toHaveCount(0);
    await page.getByTestId("catch-guard").click();
    await expect(page.getByTestId("guard-tool-web_search")).toBeVisible();
  });
});

test("back and forward move between the ways in with the URL (QA-009)", async ({ page }) => {
  await goHash(page, "connect?for=coding");
  await expect(page.getByTestId("journey-chooser")).toHaveCount(0);
  await goHash(page, "connect");
  await expect(page.getByTestId("journey-chooser")).toBeVisible();
  await goHash(page, "connect?for=production");
  await expect(page.getByTestId("journey-chooser")).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(/#\/connect$/);
  await expect(page.getByTestId("journey-chooser")).toBeVisible();
  await page.goBack();
  await expect(page).toHaveURL(/for=coding/);
  await expect(page.getByTestId("journey-chooser")).toHaveCount(0);
});
