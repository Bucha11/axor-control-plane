// Config Builder — bring your agent, leave governed (ConfigBuilder.tsx). A
// (simulated) code upload discovers the agent's tools; the operator assigns a
// consequence class to each, and the builder emits a replayable config.
import { expect, test } from "@playwright/test";
import { goHash } from "./helpers";

test.describe("config builder", () => {
  test("discovers tools, classifies them, and previews the config", async ({ page }) => {
    await goHash(page, "config-builder");
    await expect(page.getByRole("heading", { name: "Bring your agent. Leave governed." })).toBeVisible();

    // Simulated upload → detection.
    await page.getByText("Drop your agent folder or tools file").click();
    await expect(page.getByRole("heading", { name: /Found \d+ tools/ })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("web_search", { exact: true })).toBeVisible();

    // Every detected tool starts unclassified ("?"); assign READ to each until
    // the preview unlocks (unclassified → 0).
    for (let i = 0; i < 6; i++) {
      const readBtn = page.getByRole("button", { name: "READ", exact: true }).first();
      if (!(await readBtn.isVisible().catch(() => false))) break;
      await readBtn.click();
    }
    await page.getByRole("button", { name: /Preview config/ }).click();
    await expect(page.getByRole("heading", { name: "Here's what this config means." })).toBeVisible();
  });

  test("offers a hand-declared path too", async ({ page }) => {
    await goHash(page, "config-builder");
    await expect(page.getByRole("button", { name: /declare sinks by hand instead/ })).toBeVisible();
  });
});
