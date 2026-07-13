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

// The demo landing: the single-agent story stays the hero (operator
// decision); the two-tree containment story is the second screen.
test.describe("demo landing", () => {
  test("single-agent hero leads; two-tree containment is the second screen", async ({ page }) => {
    await page.goto("/demo.html");
    // hero: the single-agent story
    await expect(page.getByRole("heading", { level: 1, name: /Your agent lies when its tools fail/ })).toBeVisible();
    // second screen: the multi-agent two-tree story, both worlds
    await expect(page.getByRole("heading", { level: 2, name: /Watch the lie spread/ })).toBeVisible();
    await expect(page.getByText("UNGOVERNED")).toBeVisible();
    await expect(page.getByText("GOVERNED", { exact: true })).toBeVisible();

    // the two-tree recording plays to the divergence + containment table
    await page.getByRole("button", { name: /Run the recording/ }).nth(1).click();
    await expect(page.getByText("FABRICATION ESCAPED")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("CONTAINED", { exact: true })).toBeVisible();
    await expect(page.getByText("1/1 boundaries held")).toBeVisible();
    await expect(page.getByText("fabricated_failure")).toBeVisible();
  });
});
