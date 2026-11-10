// A phone is a real way people open the Control Plane — a link in a chat, a
// look at a run from a bus. Every surface has to fit the screen: the page used
// to be wider than the phone, so it scrolled sideways and showed white beyond
// the app.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

const ROUTES = [
  "home", "eval", "replay", "regression", "control", "get-started",
  "config-builder", "health", "pricing", "settings", "evidence", "catch", "explain", "prevent", "govern", "connect",
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
    await setConnection(page, { mode: "demo" });
    await page.route("**/v1/auth/status", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ auth_enabled: true, authenticated: false, scopes: [] }),
      }),
    );
    await goHash(page, "home");
    for (const id of ["Agents", "Policy", "Caught lies", "Replay"]) {
      await expect(page.getByRole("button", { name: id, exact: true })).toBeInViewport();
    }
    await expect(page.getByRole("button", { name: "sign in", exact: true })).toBeInViewport();
    await expect(page.getByRole("button", { name: "Safe to ship?", exact: true })).toBeInViewport();
  });

  test("a first visit shows both ways in without scrolling", async ({ page }) => {
    await goHash(page, "home");
    for (const name of [/See it govern an example fleet/, /Govern my own agent/]) {
      const box = await page.getByRole("button", { name }).boundingBox();
      expect(box!.y + box!.height).toBeLessThanOrEqual(844);
    }
  });

  test("a connected, signed-out phone still fits (demo badge + sign in)", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await page.route("**/v1/auth/status", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ auth_enabled: true, authenticated: false, scopes: [] }),
      }),
    );
    await goHash(page, "evidence");
    await page.waitForLoadState("networkidle");
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  });
});

// Between a phone and a wide screen the header switches from one row to two.
// It used to switch at 760px, and from ~760 to ~1000 the nav ran into the
// account controls.
test.describe("header at every width", () => {
  for (const width of [800, 900, 1000, 1024, 1101, 1280, 1400, 1440]) {
    test(`nav and account controls do not overlap at ${width}px`, async ({ page }) => {
      await setConnection(page, { mode: "demo" });
      await page.route("**/v1/auth/status", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ auth_enabled: true, authenticated: false, scopes: [] }),
        }),
      );
      await page.setViewportSize({ width, height: 600 });
      await goHash(page, "evidence");
      await page.waitForLoadState("networkidle");
      const overlap = await page.evaluate(() => {
        const nav = document.querySelector(".app-nav")!.getBoundingClientRect();
        const right = document.querySelector(".app-header-right")!.getBoundingClientRect();
        return !(nav.right <= right.left || nav.bottom <= right.top || right.bottom <= nav.top);
      });
      expect(overlap).toBe(false);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
    });
  }
});
