// The product's example never changes the workspace's real data, and never
// counts as the workspace's own work (QA-194, QA-215, QA-216).
import { expect, test, type Page } from "@playwright/test";
import { BACKEND, goHash, setConnection } from "./helpers";

async function journey(page: Page, j: string) {
  await page.addInitScript((value) => {
    const raw = localStorage.getItem("axor-app");
    const data = raw ? JSON.parse(raw) : { state: {}, version: 0 };
    data.state.journey = value;
    data.state.menuAll = false;
    localStorage.setItem("axor-app", JSON.stringify(data));
  }, j);
}

const teamSkill = (name: string, example: boolean) => ({
  name, description: "", example, skill_md: "", live: null,
  latest: { version: "v1", by: "", ts: "2026-11-27T10:00:00Z", check: null, files: ["SKILL.md"] },
});

test("QA-216: the example skill does not count as waiting in the menu; the team's own does", async ({ page }) => {
  let team = [teamSkill("changelog-entries", true)];
  await page.route("**/v1/coding/skills", (route) => route.fulfill({ json: { off: [], attempts: {}, team } }));
  await setConnection(page, { mode: "adapter" });
  await journey(page, "coding");
  await goHash(page, "skills");
  await expect(page.getByTestId("sidebar").getByRole("button", { name: /^Skills/ })).toBeVisible();
  await page.waitForLoadState("networkidle");
  await expect(page.getByTestId("skills-pending")).toHaveCount(0);
  team = [...team, teamSkill("release-notes", false)];
  await page.reload();
  await expect(page.getByTestId("skills-pending")).toHaveText("1");
});

test("QA-215: Settings → start tour on a coding workspace runs the coding tour and leaves the workspace as it was", async ({ page }) => {
  await setConnection(page, { mode: "adapter" });
  await page.addInitScript(() => {
    const data = JSON.parse(localStorage.getItem("axor-app") ?? '{"state":{},"version":0}');
    if (!data.state.tourSetUp) {   // only the first load: the app's own writes count after it
      Object.assign(data.state, { journey: "coding", menuAll: false, tourSide: "production", tourSetUp: true });
      localStorage.setItem("axor-app", JSON.stringify(data));
    }
  });
  await goHash(page, "settings?tab=learn");
  await page.getByRole("button", { name: "start tour", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "guided tour" });
  await expect(dialog.getByText("Your coding agents, on one plane")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("sidebar").getByRole("button", { name: /^Skills/ })).toBeVisible();
  await dialog.getByRole("button", { name: "skip tour" }).click();
  await expect(dialog).toHaveCount(0);
  const state = await page.evaluate(() => JSON.parse(localStorage.getItem("axor-app") ?? "{}").state);
  expect(state.journey).toBe("coding");
  expect(state.connection.mode).toBe("adapter");
  await expect(page.getByTestId("sidebar").getByRole("button", { name: /^Skills/ })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("sidebar").getByRole("button", { name: /^Skills/ })).toBeVisible();
  await expect(page.getByTestId("sidebar").getByRole("button", { name: /^Safe to ship/ })).toHaveCount(0);
});
