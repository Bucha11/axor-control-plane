// Get started — the proxy onboarding (Onboarding.tsx): declare tools → point the
// agent at the proxied URLs → prove the plumbing. The depth toggle sells the
// adapter's extra surfaces honestly.
import { expect, test } from "@playwright/test";
import { goHash } from "./helpers";

test.describe("onboarding", () => {
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
