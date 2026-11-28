// Pricing, Both ways in: how many developers run coding agents, how many
// production agents — the cheapest plan and why, from the list prices
// (src/quote.ts, the same numbers as the backend's ee/pricing.py).
import { expect, test } from "@playwright/test";
import { goHash, setConnection, answerAsk } from "./helpers";

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

test("the upsell only climbs the ladder, the cap is said, a large fleet is Enterprise (QA-013)", async ({ page }) => {
  await goHash(page, "pricing?for=both");
  const calc = page.getByTestId("pricing-calculator");
  const set = async (devs: number | string, agents: number | string) => {
    await calc.getByTestId("calc-devs").fill(String(devs));
    await calc.getByTestId("calc-agents").fill(String(agents));
  };
  // Team is cheapest; its seats already are Business, so the next rung is Security
  await set(10, 10);
  await expect(calc.getByTestId("calc-best")).toContainText("Team Workspace — $699 / month");
  await expect(calc.getByTestId("calc-next")).toContainText("Security Workspace is $1,201 more");
  await expect(calc.getByTestId("calc-next")).not.toContainText("Business");
  // past the threshold: no five-figure month, a contract
  await set(10, 201);
  await expect(calc.getByTestId("calc-best")).toContainText("talk to us — Enterprise");
  await expect(calc.getByTestId("calc-lines")).toHaveCount(0);
  await set(101, 1);
  await expect(calc.getByTestId("calc-best")).toContainText("talk to us — Enterprise");
  // a number past the cap is held at it, and said so
  await expect(calc.getByTestId("calc-capped")).toHaveCount(0);
  await set(1, 1_000_000);
  await expect(calc.getByTestId("calc-agents")).toHaveValue("10000");
  await expect(calc.getByTestId("calc-capped")).toContainText("counts up to 10,000");
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

test("a per-developer checkout says why nothing is due today, and knows who is paying (QA-014)", async ({ page }) => {
  await licensedTo(page, "Acme Inc");
  // signed in: who is signed in lives in the session half of the store
  await page.addInitScript(() => {
    window.localStorage.setItem("axor-app", JSON.stringify({ state: {}, version: 0 }));
    window.sessionStorage.setItem("axor-app", JSON.stringify(
      { state: { identityEmail: "buyer@acme.test" }, version: 0 }));
  });
  await goHash(page, "pricing?for=both");
  // Coding and Business bill per developer later; Team and Security charge today
  await expect(page.getByTestId("tier-coding").getByTestId("card-billed-later"))
    .toContainText("billed per developer at the end of each month");
  await expect(page.getByTestId("tier-business").getByTestId("card-billed-later")).toBeVisible();
  await expect(page.getByTestId("tier-team").getByTestId("card-billed-later")).toHaveCount(0);
  await page.getByTestId("cta-business").click();
  await expect.poll(() => page.evaluate(() =>
    ((window as unknown as { __paddle?: unknown[] }).__paddle ?? []).at(-1)))
    .toEqual(["open", { items: [{ priceId: "pri_business", quantity: 1 }],
                        customData: { org: "Acme Inc", hosted: false },
                        customer: { email: "buyer@acme.test" } }]);
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

test("each rung keeps more run history", async ({ page }) => {
  await goHash(page, "pricing?for=both");
  for (const words of ["runs kept 7 days", "runs kept 30 days", "runs kept 90 days", "runs kept a year",
                       "runs kept as long as you choose"]) {
    await expect(page.getByText(words).first()).toBeVisible();
  }
});

// QA-131/132/133/136: the plan is the owner's; a workspace paid by card
// changes its one subscription instead of buying a second; a payment says
// what happens next; a payment page that cannot load says so.
const signedInAs = async (page: import("@playwright/test").Page, scopes: string[]) => {
  await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
    auth_enabled: true, authenticated: true, scopes } }));
  await page.addInitScript(() => {
    window.localStorage.setItem("axor-app", JSON.stringify({ state: {}, version: 0 }));
    window.sessionStorage.setItem("axor-app", JSON.stringify(
      { state: { identityEmail: "someone@acme.test" }, version: 0 }));
  });
};

test("a viewer is not offered the workspace's checkout (QA-132)", async ({ page }) => {
  await licensedTo(page, "org_acme", true);
  await signedInAs(page, ["read"]);
  await goHash(page, "pricing?for=both");
  await expect(page.getByTestId("tier-team").getByTestId("card-owner-only"))
    .toContainText("the workspace owner chooses the plan");
  await expect(page.getByTestId("cta-team")).toHaveCount(0);
});

test("on a plan paid by card, another rung changes the subscription, and says when it is on (QA-131, QA-133)", async ({ page }) => {
  let tier = "coding";
  await page.route("**/v1/license/status", (r) => r.fulfill({ json: {
    active: true, vendor_key_configured: true, hosted: true, auto_renewal: true,
    licensed_to: "org_acme", workspace_tier: tier } }));
  await page.route("https://cdn.paddle.com/**", (r) => r.fulfill({
    contentType: "application/javascript", body: PADDLE_STUB }));
  const asked: unknown[] = [];
  await page.route("**/v1/license/plan", async (r) => {
    asked.push(r.request().postDataJSON());
    tier = "business";
    await r.fulfill({ json: { from: "coding", to: "business" } });
  });
  await signedInAs(page, ["read", "ingest", "operate", "admin"]);
  await goHash(page, "pricing?for=both");
  await expect(page.getByTestId("tier-coding").getByTestId("card-in-force")).toHaveText("your plan");
  await expect(page.getByTestId("cta-coding")).toHaveCount(0);
  await expect(page.getByTestId("cta-business")).toHaveText("Switch to Business");
  await page.getByTestId("cta-business").click();
  await answerAsk(page);
  await expect.poll(() => asked).toEqual([{ tier: "business" }]);
  await expect(page.getByTestId("plan-note")).toContainText("Business is on for this workspace", { timeout: 15_000 });
  // nothing was bought: no checkout opened
  expect(await page.evaluate(() => (window as unknown as { __paddle?: unknown[] }).__paddle ?? [])).toEqual([]);
});

test("a payment page that cannot load says so (QA-136)", async ({ page }) => {
  await page.route("**/v1/license/status", (r) => r.fulfill({ json: {
    active: false, vendor_key_configured: true, hosted: true, licensed_to: "org_acme" } }));
  await page.route("https://cdn.paddle.com/**", (r) => r.abort());
  await goHash(page, "pricing?for=coding");
  await page.getByTestId("cta-coding").click();
  await expect(page.getByTestId("plan-note")).toContainText("the payment page could not load");
  await expect(page).toHaveURL(/#\/pricing/);
});
