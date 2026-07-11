// The guided tour: a spotlight walkthrough that navigates the funnel itself
// (Home → Eval → Replay → Control → Regression → back to the learn toggle).
// Starting it connects demo-mode and seeds example data so every stop is real.
import { expect, test } from "@playwright/test";
import { goHash } from "./helpers";

test.describe("tour", () => {
  test("walks the whole funnel and finishes", async ({ page }) => {
    // Fresh store: the Home nudge offers the tour.
    await goHash(page, "home");
    await page.getByRole("button", { name: "Take the tour" }).click();

    const dialog = page.getByRole("dialog", { name: "guided tour" });
    await expect(dialog.getByText("The 60-second tour")).toBeVisible();
    await expect(dialog.getByText("1 / 7")).toBeVisible();

    const next = dialog.getByRole("button", { name: "next →" });
    await next.click();
    await expect(dialog.getByText("Three ways in")).toBeVisible();

    await next.click();
    await expect(dialog.getByText(/break a tool, catch the lie/)).toBeVisible();
    await expect(page).toHaveURL(/#\/eval/);

    await next.click();
    await expect(dialog.getByText("Replay — question any run")).toBeVisible();
    await expect(page).toHaveURL(/#\/replay/);

    await next.click();
    await expect(dialog.getByText("Control — operate live agents")).toBeVisible();
    await expect(page).toHaveURL(/#\/control/);

    await next.click();
    await expect(dialog.getByText(/safe to ship\?/)).toBeVisible();
    await expect(page).toHaveURL(/#\/regression/);

    await next.click();
    await expect(dialog.getByText("Learn as you go")).toBeVisible();
    await expect(dialog.getByText("7 / 7")).toBeVisible();

    await dialog.getByRole("button", { name: "done" }).click();
    await expect(page.getByRole("dialog", { name: "guided tour" })).toHaveCount(0);
  });

  test("back steps backwards and skip exits at any point", async ({ page }) => {
    await goHash(page, "home");
    await page.getByRole("button", { name: "Take the tour" }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });

    await dialog.getByRole("button", { name: "next →" }).click();
    await expect(dialog.getByText("Three ways in")).toBeVisible();
    await dialog.getByRole("button", { name: "back" }).click();
    await expect(dialog.getByText("The 60-second tour")).toBeVisible();

    await dialog.getByRole("button", { name: "skip tour" }).click();
    await expect(page.getByRole("dialog", { name: "guided tour" })).toHaveCount(0);
  });

  test("is restartable from Settings → LEARN MODE", async ({ page }) => {
    await goHash(page, "settings");
    await page.getByRole("button", { name: "start tour", exact: true }).click();
    // The tour takes over and returns to Home for step 1.
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    await expect(dialog.getByText("The 60-second tour")).toBeVisible();
    await expect(page).toHaveURL(/#\/home/);
    await dialog.getByRole("button", { name: "skip tour" }).click();
  });
});
