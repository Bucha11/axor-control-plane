// The guided tour: one per kind of agent on Home, on that side's example data,
// ending at its way in. Starting it never swaps Home for another page.
import { expect, test } from "@playwright/test";
import { goHash } from "./helpers";

test.describe("tour", () => {
  test("production: the example fleet, policy, caught lies, replay, safe to ship — then the way in", async ({ page }) => {
    await goHash(page, "home?for=production");
    await page.getByRole("button", { name: "Take the tour" }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    const next = dialog.getByRole("button", { name: "next →" });

    await expect(dialog.getByText("Your agents, live")).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("1 / 7")).toBeVisible();
    await expect(page).toHaveURL(/#\/govern$/);
    // (the fleet's story shows when only the example is on the plane; this
    // suite's plane is shared, so the card may center instead)
    for (const [title, url] of [
      ["Pause, stop, cap", /#\/govern$/],
      ["Policy: what each tool may do", /#\/policy$/],
      ["Caught lies", /#\/evidence$/],
      ["Replay: how it happened", /#\/explain/],
      ["Safe to ship?", /#\/prevent$/],
      ["Now yours", /#\/home\?for=production$/],
    ] as const) {
      await next.click();
      await expect(dialog.getByText(title, { exact: true })).toBeVisible();
      await expect(page).toHaveURL(url);
    }
    // Home is still the two ways in, with a line back to the example
    await expect(page.getByTestId("first-visit")).toBeVisible();
    await expect(page.getByTestId("home-back")).toBeVisible();
    await dialog.getByRole("button", { name: /Govern my production agent/ }).click();
    await expect(page).toHaveURL(/#\/connect\?for=production$/);
    await expect(page.getByRole("dialog", { name: "guided tour" })).toHaveCount(0);
  });

  test("coding: the example sessions and the tree — then connect", async ({ page }) => {
    await goHash(page, "home");
    await page.getByRole("button", { name: "Take the tour" }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    const next = dialog.getByRole("button", { name: "next →" });
    await expect(dialog.getByText("Your coding agents, on one plane")).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("1 / 6")).toBeVisible();
    await next.click();
    await next.click();
    await expect(dialog.getByText("A team on one task")).toBeVisible();
    await expect(page).toHaveURL(/#\/trees\/tree-example-coding$/);
    await next.click();
    await next.click();
    await expect(dialog.getByText("Before you install a skill")).toBeVisible();
    await expect(page).toHaveURL(/#\/skills$/);
    await next.click();
    await expect(dialog.getByText("Now yours")).toBeVisible();
    await dialog.getByRole("button", { name: "done" }).click();
    await expect(page.getByRole("dialog", { name: "guided tour" })).toHaveCount(0);
  });

  test("back steps backwards and skip exits at any point", async ({ page }) => {
    await goHash(page, "home?for=production");
    await page.getByRole("button", { name: "Take the tour" }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    await expect(dialog.getByText("Your agents, live")).toBeVisible({ timeout: 15_000 });
    await dialog.getByRole("button", { name: "next →" }).click();
    await expect(dialog.getByText("Pause, stop, cap")).toBeVisible();
    await dialog.getByRole("button", { name: "back" }).click();
    await expect(dialog.getByText("Your agents, live")).toBeVisible();
    await dialog.getByRole("button", { name: "skip tour" }).click();
    await expect(page.getByRole("dialog", { name: "guided tour" })).toHaveCount(0);
  });

  test("is restartable from Settings → LEARN MODE", async ({ page }) => {
    await goHash(page, "settings");
    await page.getByRole("button", { name: "start tour", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    await expect(dialog.getByText("Your agents, live")).toBeVisible({ timeout: 15_000 });
    await dialog.getByRole("button", { name: "skip tour" }).click();
  });
});
