// The application shell: funnel entry, nav, deep links, the "more…" menu, and
// the connection badge (App.tsx). These are the spine every other surface hangs
// off — if routing or the store hydration breaks, everything else does too.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe("shell", () => {
  test("a first visit leads with governance: a tab per kind of agent, a way in each, the recording — no menu", async ({ page }) => {
    await goHash(page, "home");
    await expect(page.getByTestId("first-visit")).toBeVisible();
    // coding agents first: its own headline, way in, example and recording
    await expect(page.getByText("Govern Claude Code and Codex.")).toBeVisible();
    await expect(page.getByTestId("home-connect-coding")).toBeVisible();
    await expect(page.getByTestId("home-example-coding")).toBeVisible();
    await expect(page.getByTestId("coding-demo-contain")).toBeVisible({ timeout: 20_000 });
    // the production tab has its own
    await page.getByTestId("home-tab-production").click();
    await expect(page.getByText("Govern your production agents.")).toBeVisible();
    await expect(page.getByTestId("demo-govern")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: /Explore an example fleet/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /Govern my production agent/ })).toBeVisible();
    // no menu before there is something in it — only a quiet way to look around
    await expect(page.getByTestId("sidebar")).toHaveCount(0);
    await expect(page.locator(".app-nav")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "settings" })).toHaveCount(0);
    await expect(page.getByTestId("home-look-around")).toContainText("Safe to ship?");
  });

  test("looking around first: a section opens with the menu, and Home keeps it", async ({ page }) => {
    await goHash(page, "home?for=production");
    await page.getByTestId("look-policy").click();
    await expect(page).toHaveURL(/#\/policy$/);
    const menu = page.getByTestId("sidebar").or(page.locator(".app-nav"));
    await expect(menu.first()).toBeVisible();
    await page.locator(".app-logo").or(page.getByTestId("sidebar").getByText("AXOR")).first().click();
    await expect(page).toHaveURL(/#\/home$/);
    await expect(page.getByTestId("first-visit")).toBeVisible();
    await expect(menu.first()).toBeVisible();
  });

  test("the example fleet opens on the governed agents, with the menu", async ({ page }) => {
    await goHash(page, "home?for=production");
    await page.getByRole("button", { name: /Explore an example fleet/ }).click();
    await expect(page).toHaveURL(/#\/govern$/);
    await expect(page.getByRole("button", { name: "Agents", exact: true })).toHaveAttribute("aria-current", "page");
  });

  test("the no-code way in still catches a made-up answer", async ({ page }) => {
    await goHash(page, "home?for=production");
    await page.getByRole("button", { name: /catch a made-up answer/ }).click();
    // ?auto=1 starts it, and is dropped once it has (QA-387)
    await expect(page).toHaveURL(/#\/catch(\?auto=1)?$/);
    // the test screen belongs to Chaos tests
    await expect(page.getByRole("button", { name: "Chaos tests", exact: true })).toHaveAttribute("aria-current", "page");
    // the run lands, and the button that started it is ready again — it used
    // to spin forever beside the result (an auto-start on a torn-down mount).
    // A real run through the proxy: under the full suite's load 15 s was
    // sometimes not enough (QA-123), as the other catch tests already allow.
    await expect(page.getByText(/FABRICATED TOOL RESULT/).first()).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("button", { name: /Run the test/ })).toBeEnabled();
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
    await expect(page.getByText("Govern Claude Code and Codex.")).toBeVisible();
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
    await expect(page.getByText("Govern Claude Code and Codex.")).toBeVisible();
    await expect(page.getByRole("button", { name: "sign in", exact: true })).toHaveCount(0);
  });

  test("governing your own agent starts at connect", async ({ page }) => {
    await goHash(page, "home?for=production");
    await page.getByRole("button", { name: /Govern my production agent/ }).click();
    await expect(page).toHaveURL(/#\/connect\?for=production$/);
    // first how: wrap it (guards) or a proxy (watches)
    await expect(page.getByTestId("prod-way-chooser")).toBeVisible();
  });

  test("away from Home, an unconnected visit offers to connect in the header", async ({ page }) => {
    await goHash(page, "explain");
    await page.getByRole("button", { name: "connect", exact: true }).click();
    await expect(page).toHaveURL(/#\/connect$/);
    // the header does not know which way in: it asks
    await expect(page.getByTestId("journey-chooser")).toBeVisible();
  });

  test("primary nav is the product's loop, in plain words", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "home");
    for (const [label, route] of [
      ["Agents", "govern"], ["Policy", "policy"], ["Incidents", "evidence"],
      ["Replay", "explain"], ["Safe to ship?", "prevent"],
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
      ["eval", "Chaos tests"], ["replay", "Replay"], ["regression", "Safe to ship?"],
      ["control", "Agents"], ["health", "Agents"], ["config-builder", "Policy"], ["get-started", ""],
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
      ["config-builder", /What each of your agent's tools may do/],
      ["regression", /every case you kept is replayed/],
      ["settings", /Settings/],
      ["pricing", /Pricing/],
    ];
    for (const [hash, expected] of cases) {
      await goHash(page, hash);
      await expect(page.getByText(expected).first()).toBeVisible();
    }
  });

  test("the connection badge reflects the persisted mode", async ({ page }) => {
    await page.route("**/v1/plane/nodes", (r) => r.fulfill({ json: [{ node_id: "agent-1", desired: null, facts: [],
      reported: { applied_version: 0, level: "NORMAL", budget_remaining: null, updated_ts: new Date().toISOString() } }] }));
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "home");
    await expect(page.getByText("connected · your agents").first()).toBeVisible();
  });

  test("no agent reporting yet is not \"connected\" (QA-485)", async ({ page }) => {
    // a key minted, an agent named: nothing on the plane has reported
    await page.route("**/v1/plane/nodes", (r) => r.fulfill({ json: [{ node_id: "agent-1", desired: null, facts: [],
      reported: null }] }));
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "settings");
    await expect(page.getByRole("button", { name: "connection: no agent reporting yet" })).toBeVisible();
    await expect(page.getByText("connected · your agents")).toHaveCount(0);
  });
});
