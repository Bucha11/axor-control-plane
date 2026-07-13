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

// The demo landing recomposed per spec v2 decision v2-18: the two-tree
// containment story is the hero; the single-agent split is the second screen.
test.describe("demo landing (v2 hero)", () => {
  test("two-tree containment leads; single-agent split demoted below", async ({ page }) => {
    await page.goto("/demo.html");
    // hero: the multi-agent story, both worlds
    await expect(page.getByRole("heading", { name: /One bad tool call\. Three agents\./ })).toBeVisible();
    await expect(page.getByText("UNGOVERNED")).toBeVisible();
    await expect(page.getByText("GOVERNED", { exact: true })).toBeVisible();
    // second screen: the single-agent story, demoted to an h2
    await expect(page.getByRole("heading", { level: 2, name: /Watch a single one get caught/ })).toBeVisible();

    // the hero recording plays to the divergence + containment table
    await page.getByRole("button", { name: /Run the recording/ }).first().click();
    await expect(page.getByText("FABRICATION ESCAPED")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("CONTAINED", { exact: true })).toBeVisible();
    await expect(page.getByText("1/1 boundaries held")).toBeVisible();
    await expect(page.getByText("fabricated_failure")).toBeVisible();
  });
});
