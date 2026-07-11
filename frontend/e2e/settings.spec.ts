// Settings — notifications, connection, and the EE license check (Settings.tsx).
// The notification subscribe hits the real backend; the license verify rejects
// a bad payload offline.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe("settings", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
  });

  test("subscribes a webhook to a trigger", async ({ page }) => {
    await goHash(page, "settings");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await page.getByPlaceholder("https://hooks.example/… (JSON POST)").fill("https://sink.e2e/hook");
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: /Subscribe/ }).click();
    await expect(page.getByRole("button", { name: "subscribed" })).toBeVisible();
  });

  test("surfaces the dead-letter log (delivery honesty)", async ({ page }) => {
    await goHash(page, "settings");
    // The failure-honesty log is always surfaced; its contents depend on prior
    // deliveries, so assert the section, not emptiness.
    await expect(page.getByText("DEAD-LETTER LOG")).toBeVisible();
  });

  test("rejects an invalid EE license offline", async ({ page }) => {
    await goHash(page, "settings");
    await page.getByPlaceholder('{"license": {...}, "sig": "…"}').fill('{"license":{},"sig":"deadbeef"}');
    await page.getByPlaceholder("vendor public key (hex)").fill("00".repeat(32));
    await page.getByRole("button", { name: /Verify license/ }).click();
    // A 4xx from the verify endpoint surfaces as a red error line.
    await expect(page.getByText(/^\d{3}\b/)).toBeVisible();
  });
});
