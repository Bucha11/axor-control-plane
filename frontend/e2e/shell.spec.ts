// The application shell: funnel entry, nav, deep links, the "more…" menu, and
// the connection badge (App.tsx). These are the spine every other surface hangs
// off — if routing or the store hydration breaks, everything else does too.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe("shell", () => {
  test("home is the funnel entry with the slogan and value line", async ({ page }) => {
    await goHash(page, "home");
    await expect(page.getByRole("button", { name: "CONTROL PLANE" })).toBeVisible();
    // The three-word slogan (steel / amber / green) lives in separate spans.
    for (const word of ["Eval.", "Control.", "Protect."]) {
      await expect(page.getByText(word, { exact: true })).toBeVisible();
    }
    await expect(page.getByText("Your agent lies when its tools fail.")).toBeVisible();
    await expect(
      page.getByText("the EvidenceCase is the artifact", { exact: false }),
    ).toBeVisible();
  });

  test("an unconnected visit reads as not connected", async ({ page }) => {
    await goHash(page, "home");
    await expect(page.getByText("not connected")).toBeVisible();
  });

  test("primary nav is the loop: eval / replay / regression, with control greyed until adapter", async ({ page }) => {
    await goHash(page, "home");
    for (const id of ["eval", "replay", "regression"]) {
      await expect(page.getByRole("button", { name: id, exact: true })).toBeVisible();
    }
    // Control is shown as the fourth rung but locked until an adapter connection.
    await expect(page.getByRole("button", { name: "control", exact: true })).toBeVisible();
  });

  test("the more… menu reaches a secondary surface (pricing)", async ({ page }) => {
    await goHash(page, "home");
    await page.getByText("more…", { exact: true }).click();
    await page.getByRole("button", { name: "pricing", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Pricing" })).toBeVisible();
    await expect(page).toHaveURL(/#\/pricing/);
  });

  test("every surface is addressable by a deep link", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    const cases: [string, RegExp][] = [
      ["get-started", /What tools does your agent use\?/],
      ["config-builder", /Bring your agent\. Leave governed\./],
      ["regression", /candidate/],
      ["settings", /Settings/],
      ["pricing", /Pricing/],
    ];
    for (const [hash, expected] of cases) {
      await goHash(page, hash);
      await expect(page.getByText(expected).first()).toBeVisible();
    }
  });

  test("the connection badge reflects the persisted mode", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "home");
    await expect(page.getByText("adapter · full governance").first()).toBeVisible();
  });
});
