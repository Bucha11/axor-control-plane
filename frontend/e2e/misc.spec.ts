// The remaining secondary surfaces: pricing (the monetization lines) and health
// (probe baseline). Light coverage — these are mostly static, but they must
// render and stay on-message.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe("secondary surfaces", () => {
  test("pricing states the two monetization lines", async ({ page }) => {
    await goHash(page, "pricing");
    await expect(page.getByRole("heading", { name: "Pricing" })).toBeVisible();
    await expect(
      page.getByText("anything that makes an agent safer is free forever", { exact: false }),
    ).toBeVisible();
    await expect(
      page.getByText("you pay only for how YOUR ORG runs it", { exact: false }),
    ).toBeVisible();
  });

  test("health reports the probe baseline", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "health");
    await expect(
      page.getByText(/Agent behavior is on baseline\.|One probe family is drifting\./),
    ).toBeVisible();
  });
});
