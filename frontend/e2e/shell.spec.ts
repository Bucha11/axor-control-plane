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

  test("the product is visible first; sign-in is one click from the header", async ({ page }) => {
    // a deployment with auth ON (this suite's backend runs with it off)
    await page.route("**/v1/auth/status", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ auth_enabled: true, authenticated: false, scopes: [] }),
      }),
    );
    await goHash(page, "home");
    // No wall: the funnel is on screen before anyone has an account.
    await expect(page.getByText("Your agent lies when its tools fail.")).toBeVisible();
    await expect(page.getByLabel("email")).toHaveCount(0);
    // ...and signing in is not a trip to Settings.
    await page.getByRole("button", { name: "sign in", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "sign in" });
    await expect(dialog.getByLabel("email")).toBeVisible();
    await expect(dialog.getByLabel("password")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(dialog).toHaveCount(0);
  });

  test("with auth off (a local run) there is no sign-in to offer", async ({ page }) => {
    await goHash(page, "home");
    await expect(page.getByText("Your agent lies when its tools fail.")).toBeVisible();
    await expect(page.getByRole("button", { name: "sign in", exact: true })).toHaveCount(0);
  });

  test("an unconnected visit offers to connect, as a button that says so", async ({ page }) => {
    await goHash(page, "home");
    await page.getByRole("button", { name: "connect", exact: true }).click();
    await expect(page).toHaveURL(/#\/connect$/);
    await expect(page.getByText(/What tools does your agent use\?/)).toBeVisible();
  });

  test("primary nav is the product's loop: evidence, catch, explain, prevent, govern", async ({ page }) => {
    await goHash(page, "home");
    for (const [label, route] of [
      ["Evidence", "evidence"], ["Catch", "catch"], ["Explain", "explain"],
      ["Prevent", "prevent"], ["Govern", "govern"],
    ]) {
      await page.getByRole("button", { name: label, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`#/${route}$`));
    }
    // nothing is hidden behind a "more…" any longer
    await expect(page.getByText("more…")).toHaveCount(0);
  });

  test("old links still land: eval, replay, regression, control, health, get-started", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    const cases: [string, string][] = [
      ["eval", "Catch"], ["replay", "Explain"], ["regression", "Prevent"],
      ["control", "Govern"], ["health", "Govern"], ["get-started", "Evidence"],
    ];
    for (const [hash, active] of cases) {
      await goHash(page, hash);
      if (hash === "get-started") {
        await expect(page.getByText(/What tools does your agent use\?/)).toBeVisible();
      } else {
        await expect(page.getByRole("button", { name: active, exact: true })).toHaveAttribute("aria-current", "page");
      }
    }
  });

  test("settings is one click from the header, and pricing one more", async ({ page }) => {
    await goHash(page, "home");
    await page.getByRole("button", { name: "settings" }).click();
    await expect(page).toHaveURL(/#\/settings$/);
    await page.getByRole("button", { name: /plans & pricing/ }).click();
    await expect(page.getByRole("heading", { name: "Pricing" })).toBeVisible();
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
