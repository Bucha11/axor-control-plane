// A phone is a real way people open the Control Plane — a link in a chat, a
// look at a run from a bus. Every surface has to fit the screen: the page used
// to be wider than the phone, so it scrolled sideways and showed white beyond
// the app.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

const ROUTES = [
  "home", "eval", "replay", "regression", "control", "get-started",
  "config-builder", "health", "pricing", "settings", "evidence", "catch", "explain", "prevent", "govern", "connect",
  // the screens added since, with their example data seeded (below)
  "pair", "memory", "trees", "sentinel", "chaos", "skills", "skill-evals", "skill-evals/se-example",
  "settings?tab=billing", "settings?tab=connect", "settings?tab=alerts", "settings?tab=security",
  "settings?tab=learn", "explain/ex_coding_claude",
];

test.describe("on a phone", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test.beforeAll(async ({ request }) => {
    const B = "http://127.0.0.1:8400";
    for (const seed of ["seed-coding", "seed-memory", "seed-setup-health", "seed-skill-eval", "seed-team-skill"]) {
      await request.post(`${B}/v1/demo/${seed}`);
    }
  });

  test("a pair session, and two side by side, fit a 390px screen", async ({ page, request }) => {
    const B = "http://127.0.0.1:8400";
    const pair = await (await request.post(`${B}/v1/demo/seed-pair`)).json();
    const other = await (await request.post(`${B}/v1/pair`, { data: { node_id: "codex-example-77b0e4d2" } })).json();
    for (const route of [`pair/${pair.token}`, `pair/${pair.token}?with=${other.token}`]) {
      await goHash(page, route);
      await page.getByTestId("pair-pane").first().waitFor();
      const width = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(width, route).toBeLessThanOrEqual(390);
    }
    await request.delete(`${B}/v1/pair/${other.token}`);
  });

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

  test("QA-442 / QA-444: checkboxes keep their size beside long labels, and the page has its main landmark", async ({ page }) => {
    await goHash(page, "settings?tab=alerts");
    const box = page.locator('input[type="checkbox"]').first();
    await expect(box).toBeVisible();
    const b = (await box.boundingBox())!;
    expect(b.width).toBeGreaterThanOrEqual(12);
    expect(await page.locator("main").count()).toBe(1);
  });

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
    for (const id of ["Agents", "Policy", "Incidents", "Replay"]) {
      await expect(page.getByRole("button", { name: id, exact: true })).toBeInViewport();
    }
    await expect(page.getByRole("button", { name: "sign in", exact: true })).toBeInViewport();
    await expect(page.getByRole("button", { name: "Safe to ship?", exact: true })).toBeInViewport();
  });

  test("a first visit shows the tabs and the way in without scrolling", async ({ page }) => {
    await goHash(page, "home");
    for (const id of ["home-tab-production", "home-connect-coding", "home-example-coding"]) {
      const box = await page.getByTestId(id).boundingBox();
      expect(box!.y + box!.height).toBeLessThanOrEqual(844);
    }
  });

  // QA-030: signed in, the e-mail chip pushed the header 32px past the screen
  // on every page, and opening the account menu shifted the page ("XOR").
  test("signed in, the header and the open account menu fit a 390px screen", async ({ page }) => {
    // a new workspace: nothing connected yet, so the header carries "connect" too
    await setConnection(page, { mode: "none" });
    const email = "axor-qa3-bb8542-long-name@example.com";
    await page.route("**/identity/v1/login", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ access_token: "acc-token", refresh_token: "ref-token", token_type: "Bearer",
        expires_in: 900, user: { user_id: "usr_1", email }, org: { org_id: "org_1", role: "owner", tier: "community" } }),
    }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill(email);
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await goHash(page, "govern");
    const chip = page.getByRole("button", { name: email });
    await expect(chip).toBeVisible();
    await page.waitForLoadState("networkidle");
    const scroll = () => page.evaluate(() => document.documentElement.scrollWidth);
    expect(await scroll()).toBeLessThanOrEqual(390);
    await chip.click();
    const menu = page.getByRole("dialog", { name: "account" });
    await expect(menu).toBeVisible();
    expect(await scroll()).toBeLessThanOrEqual(390);
    const box = (await menu.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(390);
    // the page did not slide under the menu: the logo still starts on screen
    expect((await page.locator(".app-logo").boundingBox())!.x).toBeGreaterThanOrEqual(0);
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

// Between a phone and a wide screen the header switches from one row to two;
// from 1000px the navigation is a sidebar instead.
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
      if (width >= 1000) {
        // wide: the navigation is a sidebar, and the page still fits
        await expect(page.getByTestId("sidebar")).toBeVisible();
        await expect(page.getByRole("button", { name: "sign in", exact: true })).toBeVisible();
        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
        return;
      }
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
