// Pricing, Both ways in: how many developers run coding agents, how many
// production agents — the cheapest plan and why, from the list prices
// (src/quote.ts, the same numbers as the backend's ee/pricing.py).
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test("both ways in: the calculator names the cheapest plan and what the next one adds", async ({ page }) => {
  await goHash(page, "pricing?for=both");
  const calc = page.getByTestId("pricing-calculator");
  const set = async (devs: number, agents: number) => {
    await calc.getByTestId("calc-devs").fill(String(devs));
    await calc.getByTestId("calc-agents").fill(String(agents));
  };
  // a coding team with one production agent: no jump to Team for it
  await set(20, 1);
  await expect(calc.getByTestId("calc-best")).toContainText("Coding — $500 / month");
  // the next plan up, and what it is for
  await expect(calc.getByTestId("calc-next")).toContainText("Business is $300 more ($800) and adds single sign-on");
  // ten production agents: Team is cheaper now
  await set(20, 10);
  await expect(calc.getByTestId("calc-best")).toContainText("Team Workspace — $1,099 / month");
  await expect(calc.getByTestId("calc-lines")).toContainText("20 developers × $40");
  await expect(calc.getByTestId("calc-lines")).toContainText("10 production agents included");
  await expect(calc.getByTestId("calc-all")).toContainText("Coding: $1,175");
  // one developer, one agent: free
  await set(1, 1);
  await expect(calc.getByTestId("calc-best")).toContainText("Community — $0 / month");
  // production only
  await set(0, 12);
  await expect(calc.getByTestId("calc-best")).toContainText("Team Workspace — $449 / month");
  await expect(calc.getByTestId("calc-all")).toContainText("Community: — the free plan governs one production agent");
  // the Coding card is on this way in too, and says what it takes
  await expect(page.getByTestId("tier-coding")).toContainText("one included, then $75 per agent");
  await expect(page.getByTestId("tier-business")).toContainText("$40 / developer / mo");
});

test("only one way in shows no calculator", async ({ page }) => {
  await goHash(page, "pricing?for=coding");
  await expect(page.getByTestId("tier-coding")).toBeVisible();
  await expect(page.getByTestId("pricing-calculator")).toHaveCount(0);
});

// Card checkout: Paddle.js stubbed, so the test sees exactly what would be
// opened — the rung's price, and the organization a license must name here.
const PADDLE_STUB = `
  window.__paddle = [];
  window.Paddle = {
    Environment: { set: (e) => window.__paddle.push(["env", e]) },
    Initialize: (o) => window.__paddle.push(["init", o.token]),
    Checkout: { open: (o) => window.__paddle.push(["open", o]) },
  };`;

async function licensedTo(page: import("@playwright/test").Page, org?: string, hosted = false) {
  await page.route("**/v1/license/status", (route) => route.fulfill({
    json: { active: false, vendor_key_configured: true, hosted,
            ...(org ? { licensed_to: org } : {}) },
  }));
  await page.route("https://cdn.paddle.com/**", (route) => route.fulfill({
    contentType: "application/javascript", body: PADDLE_STUB,
  }));
}

test("a paid plan opens card checkout naming who the license is for", async ({ page }) => {
  await licensedTo(page, "Acme Inc");
  await goHash(page, "pricing?for=coding");
  await expect(page.getByTestId("card-needs-org")).toHaveCount(0);
  await page.getByTestId("cta-coding").click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __paddle: unknown[] }).__paddle))
    .toEqual([
      ["env", "sandbox"],
      ["init", "test_e2e_token"],
      ["open", { items: [{ priceId: "pri_coding", quantity: 1 }],
                 customData: { org: "Acme Inc", hosted: false } }],
    ]);
});

test("with nobody to issue a license to, it says what is missing", async ({ page }) => {
  await licensedTo(page);
  await goHash(page, "pricing?for=coding");
  const coding = page.getByTestId("tier-coding");
  await expect(coding.getByTestId("card-needs-org")).toContainText("set AXOR_ORG");
  await expect(page.getByTestId("cta-coding")).toHaveAttribute("href", /^mailto:/);
});

test("on the hosted plane the checkout says so, for the license to be installed here", async ({ page }) => {
  await licensedTo(page, "org_acme", true);
  await goHash(page, "pricing?for=coding");
  await page.getByTestId("cta-coding").click();
  // Paddle.js loads after the click: until it has, there is no __paddle to
  // read, and the poll must go round again rather than throw
  await expect.poll(() => page.evaluate(() =>
    ((window as unknown as { __paddle?: unknown[] }).__paddle ?? []).at(-1)))
    .toEqual(["open", { items: [{ priceId: "pri_coding", quantity: 1 }],
                        customData: { org: "org_acme", hosted: true } }]);
});

test("on the hosted plane, nobody signed in is told to sign in", async ({ page }) => {
  await licensedTo(page, undefined, true);
  await goHash(page, "pricing?for=coding");
  await expect(page.getByTestId("tier-coding").getByTestId("card-needs-org"))
    .toContainText("sign in to a workspace");
});

test("a hosted guest is told to sign in, not handed a link that does nothing", async ({ page }) => {
  // the hosted posture, as e2e/guest.spec.ts puts it on the wire
  await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
    auth_enabled: true, authenticated: false, scopes: [], guest_sessions: true, guest: false } }));
  await page.route("**/v1/auth/guest", (r) => r.fulfill({ status: 201, json: {
    token: "gs_e2e_pricing", org: "guest_e2e", ttl_seconds: 1800,
    expires_at: new Date(Date.now() + 20 * 60_000).toISOString() } }));
  // what the plane answers a guest on /v1/license (guests.ACCOUNT_ONLY)
  await page.route("**/v1/license/status", (r) => r.fulfill({ status: 403, json: {
    error: "account_required", feature: "licences" } }));
  await setConnection(page, { mode: "demo" });
  await goHash(page, "pricing?for=coding");
  await expect(page.getByTestId("tier-coding").getByTestId("card-needs-org"))
    .toContainText("sign up or sign in to pay by card");
});

test("back and forward move the tabs with the URL (QA-009)", async ({ page }) => {
  await goHash(page, "pricing?for=coding");
  await expect(page.getByTestId("tier-coding")).toBeVisible();
  await goHash(page, "pricing?for=production");
  await expect(page.getByTestId("tier-coding")).toHaveCount(0);
  await page.goBack();
  await expect(page).toHaveURL(/for=coding/);
  await expect(page.getByTestId("tier-coding")).toBeVisible();
  await page.goForward();
  await expect(page.getByTestId("tier-coding")).toHaveCount(0);
});

test("the Connect copy and the Enterprise floor quote the same prices as the cards (QA-012)", async ({ page }) => {
  await goHash(page, "pricing?for=production");
  // the floor is Security's fleet: 50 nodes, $50 past them — not 10 and $75
  const floor = page.getByTestId("enterprise-floor");
  await expect(floor).toContainText("Control Plane for 50 governed nodes");
  await expect(floor).toContainText("Additional governed nodes: $50 / node / month");
  await expect(page.getByTestId("tier-security")).toContainText("+$50 / governed node / mo beyond 50");
  // Team includes no developers: each is a $40 Business seat, as its card says
  await setConnection(page, { mode: "none" });
  await page.route("**/v1/plane/nodes", (route) => route.fulfill({ json: [] }));
  await goHash(page, "connect?for=both");
  const free = page.getByTestId("coding-free");
  await expect(free).toContainText("Team Workspace, $299 / month: 10 production agents");
  await expect(free).toContainText("Business seat on top, $40");
  await expect(free).not.toContainText("$25");
});
