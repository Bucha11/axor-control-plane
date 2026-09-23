// Buying and managing a plan from the Control Plane. The e2e stack has no
// identity service, so /identity/* is stubbed per test: this exercises the UI
// flow (Pricing → sign in → checkout, return, portal), not Paddle or identity.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

const SESSION = {
  access_token: "acc-token",
  refresh_token: "ref-token",
  token_type: "Bearer",
  expires_in: 900,
  user: { user_id: "usr_1", email: "ada@acme.io" },
  org: { org_id: "org_1", role: "owner", tier: "community" },
};
const CONFIG = { enabled: true, environment: "sandbox", client_token: "ct", tiers: ["team", "security"] };

function json(status: number, body: unknown) {
  return (route: import("@playwright/test").Route) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

function status(tier: string, sub: object | null = null, canManage = false) {
  return {
    enabled: true, org_id: "org_1", tier, token_tier: tier, subscription: sub,
    is_admin: true, can_manage: canManage,
  };
}

async function signIn(page: import("@playwright/test").Page) {
  await page.getByLabel("email").fill("ada@acme.io");
  await page.getByLabel("password").fill("correct horse");
  await page.getByRole("button", { name: "log in", exact: true }).click();
  await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();
}

test.describe("plan & billing", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await page.route("**/identity/v1/billing/config", json(200, CONFIG));
    await page.route("**/identity/v1/login", json(200, SESSION));
    await page.route("**/identity/v1/me", json(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "community" }],
    }));
  });

  test("with billing off the section is not shown", async ({ page }) => {
    await page.route("**/identity/v1/billing/config", json(200, { enabled: false }));
    await goHash(page, "settings");
    await expect(page.getByText("AUTHENTICATION")).toBeVisible();
    await expect(page.getByText("PLAN & BILLING")).toHaveCount(0);
  });

  test("a free org upgrades from Settings and is sent to checkout", async ({ page }) => {
    await page.route("**/identity/v1/billing/subscription", json(200, status("community")));
    let sent: { tier: string; return_url: string } | null = null;
    await page.route("**/identity/v1/billing/checkout", async (route) => {
      sent = route.request().postDataJSON();
      await json(201, { transaction_id: "txn_1", checkout_url: "/identity/v1/billing/pay?_ptxn=txn_1" })(route);
    });
    await page.route("**/identity/v1/billing/pay?_ptxn=txn_1", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<p>paddle checkout</p>" }));
    await goHash(page, "settings");
    await signIn(page);
    await expect(page.getByText("plan Community")).toBeVisible();
    await page.getByRole("button", { name: /upgrade to Team Workspace/ }).click();
    await expect(page.getByText("paddle checkout")).toBeVisible();
    expect(sent!.tier).toBe("team");
    expect(sent!.return_url).toMatch(/#\/settings$/);
  });

  test("Pricing remembers the plan across sign-in, then opens checkout", async ({ page }) => {
    await page.route("**/identity/v1/billing/subscription", json(200, status("community")));
    let tier = "";
    await page.route("**/identity/v1/billing/checkout", async (route) => {
      tier = route.request().postDataJSON().tier;
      await json(201, { transaction_id: "txn_2", checkout_url: "/identity/v1/billing/pay?_ptxn=txn_2" })(route);
    });
    await page.route("**/identity/v1/billing/pay?_ptxn=txn_2", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<p>paddle checkout</p>" }));
    await goHash(page, "pricing");
    await page.getByRole("button", { name: "Get Security Workspace" }).click();
    await expect(page.getByText("You picked Security Workspace")).toBeVisible();
    await signIn(page);
    await expect(page.getByText("paddle checkout")).toBeVisible();
    expect(tier).toBe("security");
  });

  test("back from a successful checkout shows the plan and the portal", async ({ page }) => {
    await page.route("**/identity/v1/refresh", json(200, { ...SESSION, org: { ...SESSION.org, tier: "team" } }));
    const sub = { status: "active", tier: "team", current_period_end: "2026-10-23T00:00:00Z", scheduled_change: null };
    await page.route("**/identity/v1/billing/subscription", json(200, status("team", sub, true)));
    await page.route("**/identity/v1/billing/portal", json(200, { url: "/portal-stub" }));
    await page.route("**/portal-stub", (route) =>
      route.fulfill({ status: 200, contentType: "text/html", body: "<p>customer portal</p>" }));
    await goHash(page, "settings");
    await signIn(page);
    await page.goto("/?billing=success#/settings");
    await expect(page.getByText("Payment received — your plan is active.")).toBeVisible();
    await expect(page.getByText("plan Team Workspace · active · renews 2026-10-23")).toBeVisible();
    await expect(page).toHaveURL(/^[^?]*#\/settings/);
    await page.getByRole("button", { name: /manage billing/ }).click();
    await expect(page.getByText("customer portal")).toBeVisible();
  });

  test("a checkout error from the server is shown", async ({ page }) => {
    await page.route("**/identity/v1/billing/subscription", json(200, status("community")));
    await page.route("**/identity/v1/billing/checkout",
      json(502, { detail: "the payment provider is unavailable" }));
    await goHash(page, "settings");
    await signIn(page);
    await page.getByRole("button", { name: /upgrade to Team Workspace/ }).click();
    await expect(page.getByText("the payment provider is unavailable")).toBeVisible();
  });
});
