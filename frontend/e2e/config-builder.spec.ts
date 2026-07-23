// Config Builder — bring your agent, leave governed (ConfigBuilder.tsx). A real
// code upload (POST /v1/wrap/scan, the axor-wrap engine on the backend)
// discovers the agent's tools; the operator assigns a consequence class to
// each, and the builder emits a replayable config. When the backend lacks the
// wrap extra, the builder shows an honest "engine not installed" panel instead.
import { expect, test } from "@playwright/test";
import { goHash } from "./helpers";

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

test.describe("config builder", () => {
  test("scans uploaded code, classifies tools, and previews the config", async ({ page }) => {
    await goHash(page, "config-builder");
    await expect(page.getByRole("heading", { name: "Bring your agent. Leave governed." })).toBeVisible();

    // Real upload: feed a .py file into the hidden picker → /v1/wrap/scan.
    await page.locator('input[type="file"]').setInputFiles({
      name: "tools.py", mimeType: "text/x-python", buffer: Buffer.from(AGENT_PY),
    });

    // Either the scan succeeds (wrap engine installed on the backend) or the
    // builder shows the honest 501 panel — both are correct product behavior.
    const found = page.getByRole("heading", { name: /Found \d+ tools/ });
    const notInstalled = page.getByText("wrap engine not installed on the backend");
    await expect(found.or(notInstalled)).toBeVisible({ timeout: 15_000 });
    if (await notInstalled.isVisible()) {
      test.info().annotations.push({
        type: "note",
        description: "backend has no axor-wrap (axor-backend[wrap] extra) — 501 path verified",
      });
      return;
    }

    await expect(page.getByText("read_inbox", { exact: true })).toBeVisible();
    await expect(page.getByText("send_email", { exact: true })).toBeVisible();

    // The scanner pre-classifies confident guesses; anything left "?" gets a
    // class by hand until the preview unlocks (unclassified → 0).
    for (let i = 0; i < 6; i++) {
      const readBtn = page.getByRole("button", { name: "READ", exact: true }).first();
      if (!(await readBtn.isVisible().catch(() => false))) break;
      await readBtn.click();
    }
    await page.getByRole("button", { name: /Preview config/ }).click();
    await expect(page.getByRole("heading", { name: "Here's what this config means." })).toBeVisible();
    // The second real artifact: tool manifests via POST /v1/wrap/manifests.
    await expect(page.getByRole("button", { name: /download tool manifests/ })).toBeVisible();
  });

  test("offers a hand-declared path too", async ({ page }) => {
    await goHash(page, "config-builder");
    await expect(page.getByRole("button", { name: /declare sinks by hand instead/ })).toBeVisible();
  });
});
