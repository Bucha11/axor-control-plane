// A phone is a real way people open the Control Plane — a link in a chat, a
// look at a run from a bus. Every surface has to fit the screen: the page used
// to be wider than the phone, so it scrolled sideways and showed white beyond
// the app.
import { expect, test } from "@playwright/test";
import { goHash } from "./helpers";

const ROUTES = [
  "home", "eval", "replay", "regression", "control", "get-started",
  "config-builder", "health", "expert", "pricing", "settings",
];

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  for (const route of ROUTES) {
    test(`${route} fits a 390px screen`, async ({ page }) => {
      await goHash(page, route);
      await page.waitForLoadState("networkidle");
      const [width, offenders] = await page.evaluate(() => {
        const out: string[] = [];
        for (const el of Array.from(document.querySelectorAll("body *"))) {
          const rc = el.getBoundingClientRect();
          const kids = Array.from(el.children).some((c) => c.getBoundingClientRect().right > 391);
          if (rc.width > 0 && rc.right > 391 && !kids) {
            out.push(`${el.tagName.toLowerCase()} right=${Math.round(rc.right)} "${(el.textContent || "").trim().slice(0, 40)}"`);
          }
        }
        return [document.documentElement.scrollWidth, out.slice(0, 5)] as const;
      });
      // named, so a failure says what is too wide rather than only that something is
      expect(width, offenders.join("\n")).toBeLessThanOrEqual(390);
    });
  }

  test("the nav and the account controls are both on screen", async ({ page }) => {
    await page.route("**/v1/auth/status", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ auth_enabled: true, authenticated: false, scopes: [] }),
      }),
    );
    await goHash(page, "home");
    for (const id of ["eval", "replay", "regression"]) {
      await expect(page.getByRole("button", { name: id, exact: true })).toBeInViewport();
    }
    await expect(page.getByRole("button", { name: "sign in", exact: true })).toBeInViewport();
    await expect(page.getByText("more…")).toBeInViewport();
  });
});
