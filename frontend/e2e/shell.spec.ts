// The application shell: funnel entry, nav, deep links, the "more…" menu, and
// the connection badge (App.tsx). These are the spine every other surface hangs
// off — if routing or the store hydration breaks, everything else does too.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe("shell", () => {
  test("a first visit is one plain sentence, the recording, and two ways to try — no menu", async ({ page }) => {
    await goHash(page, "home");
    await expect(page.getByTestId("first-visit")).toBeVisible();
    await expect(page.getByText("Your AI agent makes things up when its tools break.")).toBeVisible();
    // the recording plays on its own, without a click
    await expect(page.getByTestId("demo-catch")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: /Try it on our demo agent/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Test my own agent/ })).toBeVisible();
    // none of the product's vocabulary before there is something in it
    for (const label of ["Caught lies", "Test an agent", "Replay", "Safe to ship?", "Live agents"]) {
      await expect(page.getByRole("button", { name: label, exact: true })).toHaveCount(0);
    }
    await expect(page.getByRole("button", { name: "settings" })).toHaveCount(0);
  });

  test("trying the demo agent brings the menu with it", async ({ page }) => {
    await goHash(page, "home");
    await page.getByRole("button", { name: /Try it on our demo agent/ }).click();
    await expect(page).toHaveURL(/#\/catch\?auto=1/);
    await expect(page.getByRole("button", { name: "Caught lies", exact: true })).toBeVisible();
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
    await expect(page.getByText("Your AI agent makes things up when its tools break.")).toBeVisible();
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
    await expect(page.getByText("Your AI agent makes things up when its tools break.")).toBeVisible();
    await expect(page.getByRole("button", { name: "sign in", exact: true })).toHaveCount(0);
  });

  test("testing your own agent starts at connect", async ({ page }) => {
    await goHash(page, "home");
    await page.getByRole("button", { name: /Test my own agent/ }).click();
    await expect(page).toHaveURL(/#\/connect$/);
    await expect(page.getByText(/What tools does your agent use\?/)).toBeVisible();
  });

  test("away from Home, an unconnected visit offers to connect in the header", async ({ page }) => {
    await goHash(page, "explain");
    await page.getByRole("button", { name: "connect", exact: true }).click();
    await expect(page).toHaveURL(/#\/connect$/);
    await expect(page.getByText(/What tools does your agent use\?/)).toBeVisible();
  });

  test("primary nav is the product's loop, in plain words", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "home");
    for (const [label, route] of [
      ["Caught lies", "evidence"], ["Test an agent", "catch"], ["Replay", "explain"],
      ["Safe to ship?", "prevent"], ["Live agents", "govern"],
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
      ["eval", "Test an agent"], ["replay", "Replay"], ["regression", "Safe to ship?"],
      ["control", "Live agents"], ["health", "Live agents"], ["get-started", ""],
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
    await setConnection(page, { mode: "demo" });
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
