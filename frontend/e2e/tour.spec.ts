// The guided tour: one per kind of agent on Home, on that side's example data,
// ending at its way in. Starting it never swaps Home for another page.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe("tour", () => {
  test("once the workspace's own agents report, Home offers no example and the tour skips the example-only stop", async ({ page }) => {
    await page.route("**/v1/workspace", (route) =>
      route.fulfill({ json: { journey: null, work_started: true } }));
    await goHash(page, "home?for=production");
    await expect(page.getByTestId("home-connect-production")).toBeVisible();
    await expect(page.getByTestId("home-example-fleet")).toHaveCount(0);
    await page.getByRole("button", { name: "Take the tour" }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    await expect(dialog.getByText("Pause, stop, cap", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("1 / 8")).toBeVisible();
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });

  test("production: the example fleet, policy, caught lies, replay, safe to ship — then the way in", async ({ page }) => {
    await goHash(page, "home?for=production");
    await page.getByRole("button", { name: "Take the tour" }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    const next = dialog.getByRole("button", { name: "next →" });

    await expect(dialog.getByText("Your agents, live")).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("1 / 9")).toBeVisible();
    await expect(page).toHaveURL(/#\/govern$/);
    // (the fleet's story shows when only the example is on the plane; this
    // suite's plane is shared, so the card may center instead)
    for (const [title, url] of [
      ["Pause, stop, cap", /#\/govern$/],
      ["Policy: what each tool may do", /#\/policy$/],
      ["Chaos tests", /#\/chaos$/],
      ["Incidents", /#\/evidence$/],
      ["Replay: how it happened", /#\/explain/],
      ["Safe to ship?", /#\/prevent$/],
      ["How this plane is protected", /#\/settings\?tab=security$/],
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

  test("coding: sessions, pairing, team memory, context health, shared skills, incidents, the team — then connect", async ({ page }) => {
    await goHash(page, "home");
    await page.getByRole("button", { name: "Take the tour" }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    const next = dialog.getByRole("button", { name: "next →" });
    await expect(dialog.getByText("Your coding agents, on one plane")).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("1 / 9")).toBeVisible();
    for (const [title, url] of [
      ["Pair on a session", /#\/pair\/[A-Za-z0-9_-]+$/],
      ["What your agents learned, for the whole team", /#\/memory$/],
      ["Is your setup making agents worse?", /#\/health$/],
      ["Skills: which help, shared in one click", /#\/skill-evals$/],
      ["Incidents", /#\/evidence$/],
      ["A team of agents on one task", /#\/trees\/tree-example-coding$/],
      ["Your team", /#\/settings\?tab=connect$/],
      ["Now yours", /#\/home\?for=coding$/],
    ] as const) {
      await next.click();
      await expect(dialog.getByText(title, { exact: true })).toBeVisible();
      await expect(page).toHaveURL(url);
    }
    // the stops are not empty pages: the example data was seeded for them
    await dialog.getByRole("button", { name: "back" }).click();
    await dialog.getByRole("button", { name: "back" }).click();
    await dialog.getByRole("button", { name: "back" }).click();
    await dialog.getByRole("button", { name: "back" }).click();
    await dialog.getByRole("button", { name: "back" }).click();
    await dialog.getByRole("button", { name: "back" }).click();
    await expect(page.getByTestId("memory-preview")).toContainText("uv run pytest -q");
    await dialog.getByRole("button", { name: "skip tour" }).click();
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

  test("a guest's coding tour skips the pair page a guest is refused", async ({ page }) => {
    // QA-046: step 2 opened #/pair/<token>, which tells a guest "Sign in to join
    // a pair session" — the tour showed a refusal
    await setConnection(page, { mode: "none", apiToken: "gs_e2e_guest_token" });
    await goHash(page, "home");
    await page.getByRole("button", { name: "Take the tour" }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    await expect(dialog.getByText("Your coding agents, on one plane")).toBeVisible({ timeout: 15_000 });
    await expect(dialog.getByText("1 / 8")).toBeVisible();
    await dialog.getByRole("button", { name: "next →" }).click();
    await expect(dialog.getByText("What your agents learned, for the whole team", { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/#\/memory$/);
  });

  test("Escape ends the tour, and so does leaving the step's page", async ({ page }) => {
    // QA-047: Escape did nothing, and after a sidebar click the card and the
    // dimmed backdrop stayed on top of an unrelated page
    await goHash(page, "home?for=production");
    await page.getByRole("button", { name: "Take the tour" }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    await expect(dialog.getByText("Your agents, live")).toBeVisible({ timeout: 15_000 });
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);

    await goHash(page, "home?for=production");
    await page.getByRole("button", { name: "Take the tour" }).click();
    await expect(dialog.getByText("Your agents, live")).toBeVisible({ timeout: 15_000 });
    await expect(page).toHaveURL(/#\/govern$/);
    await page.getByRole("button", { name: "Policy", exact: true }).click();
    await expect(page).toHaveURL(/#\/policy$/);
    await expect(dialog).toHaveCount(0);
  });

  test("is restartable from Settings → learn", async ({ page }) => {
    await goHash(page, "settings?tab=learn");
    await page.getByRole("button", { name: "start tour", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "guided tour" });
    await expect(dialog.getByText("Your agents, live")).toBeVisible({ timeout: 15_000 });
    await dialog.getByRole("button", { name: "skip tour" }).click();
  });
});
