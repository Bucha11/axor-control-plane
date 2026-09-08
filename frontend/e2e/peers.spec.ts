// M5: inter-federation peers — declared like sinks in the Config Builder
// (spec v2 Ch.1 §3); undeclared = L0; declaration buys discount, never label
// authority. The opaque peer node renders in the graph lens with the denied
// edge flash.
import { expect, request, test } from "@playwright/test";
import { BACKEND, goHash, setConnection, uniqueNode } from "./helpers";

const AGENT_PY = [
  "from langchain_core.tools import tool",
  "",
  "",
  "@tool",
  "def read_inbox(folder: str) -> str:",
  '    """Read the user\'s email inbox."""',
  "    return folder",
  "",
  "",
  "@tool",
  "def send_email(to: str, body: str) -> str:",
  '    """Send an email to a recipient."""',
  "    return to",
  "",
].join("\n");

test.describe("inter-federation peers", () => {
  test("config builder declares a peer; the preview states the ceiling", async ({ page }) => {
    await goHash(page, "config-builder");
    // The drop zone opens a native file chooser; drive the hidden input
    // directly, the same way config-builder.spec.ts feeds the scanner.
    await page.getByTestId("wrap-files").setInputFiles({
      name: "tools.py",
      mimeType: "text/x-python",
      buffer: Buffer.from(AGENT_PY),
    });
    await expect(page.getByRole("heading", { name: /Found \d+ tools/ })).toBeVisible({ timeout: 15_000 });
    for (let i = 0; i < 6; i++) {
      const readBtn = page.getByRole("button", { name: "READ", exact: true }).first();
      if (!(await readBtn.isVisible().catch(() => false))) break;
      await readBtn.click();
    }

    // declare an L2 peer with a discount class
    await page.getByText("inter-federation peers (A2A, optional)").click();
    await page.getByRole("button", { name: /Declare a peer/ }).click();
    await page.getByPlaceholder("peer id").fill("partner-agent");
    await page.getByPlaceholder("ed25519 pubkey (hex)").fill("ab".repeat(32));
    await page.getByRole("button", { name: "L2", exact: true }).click();
    await page.getByPlaceholder("message classes (comma)").fill("research");
    await page.getByRole("button", { name: "declare", exact: true }).click();
    await expect(page.getByText("partner-agent")).toBeVisible();

    // the preview spells out the trust ceiling, not just the declaration
    await page.getByRole("button", { name: /Preview config/ }).click();
    await expect(page.getByText(/signed assertions get a bounded discount/)).toBeVisible();
    await expect(page.getByText(/critical sinks ignore it/)).toBeVisible();
    await expect(page.getByText(/Any peer NOT declared here is L0/)).toBeVisible();
  });

  test("the graph lens renders the opaque peer and the denied edge", async ({ page }) => {
    const ctx = await request.newContext();
    await ctx.post(`${BACKEND}/v1/demo/seed-tree-run`);
    const nid = uniqueNode("peer-live");
    await ctx.post(`${BACKEND}/v1/plane/${nid}/command`, {
      data: { version: 1, state: { paused: false } },
    });
    await ctx.dispose();

    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await page.getByRole("button", { name: "graph", exact: true }).click();

    // the foreign peer renders as an opaque node…
    await expect(page.getByTestId("topo-node-partner-agent")).toBeVisible();
    await expect(page.getByText("peer · opaque")).toBeVisible();
    // …with the denied send flashed on the edge, at the boundary
    await expect(page.getByText(/DENIED · message_gate/)).toBeVisible();

    // selecting the peer shows the opaque card with ZERO intervention buttons
    await page.getByTestId("topo-node-partner-agent").click();
    await expect(page.getByText("foreign federation")).toBeVisible();
    await expect(page.getByText(/not ours to steer/)).toBeVisible();
  });
});
