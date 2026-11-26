// A hosted visitor works as a guest and keeps the work by signing up.
//
// The e2e stack runs with auth off, so the hosted posture is put on the wire
// here: /v1/auth/status says auth is on and guests are offered, the guest and
// claim routes answer as the backend does (tests/test_guests.py drives the real
// ones), and identity's signup returns a session. Everything else is the real
// backend — with auth off it serves any bearer.
import { expect, test, type Page } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

const GUEST = "gs_e2e_guest_token";

async function hosted(page: Page, opts: { accountRequired?: boolean; minutesLeft?: number } = {}) {
  const claimed: string[] = [];
  await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
    auth_enabled: true, authenticated: false, scopes: [], guest_sessions: true, guest: false } }));
  await page.route("**/v1/auth/guest", (r) => r.fulfill({ status: 201, json: {
    token: GUEST, org: "guest_e2e", ttl_seconds: 1800,
    expires_at: new Date(Date.now() + (opts.minutesLeft ?? 8) * 60_000).toISOString() } }));
  await page.route("**/v1/auth/claim-guest", async (r) => {
    claimed.push((r.request().postDataJSON() as { guest_token: string }).guest_token);
    await r.fulfill({ json: { claimed: true, moved: { runs: 2, desired_state: 1, events: 9 } } });
  });
  await page.route("**/identity/v1/signup-mode", (r) => r.fulfill({ json: { mode: "open", signup: true } }));
  await page.route("**/identity/v1/signup", (r) => r.fulfill({ json: {
    access_token: "eyJ.user.token", refresh_token: "rt_1", token_type: "bearer",
    user: { id: "usr_1", email: "new@acme.io" } } }));
  if (opts.accountRequired) {
    await page.route("**/v1/notifications/subscriptions", (r) => r.fulfill({ status: 403, json: {
      error: "account_required", feature: "notifications",
      detail: "notifications need an account" } }));
  }
  return claimed;
}

test.describe("guest", () => {
  test("a fresh guest is not shown a clock: the header just offers to sign in", async ({ page }) => {
    await hosted(page, { minutesLeft: 29 });
    await setConnection(page, { mode: "demo" });
    await goHash(page, "govern");
    await expect(page.getByRole("button", { name: "sign in", exact: true })).toBeVisible();
    await expect(page.getByText(/min left/)).toHaveCount(0);
  });

  test("a visitor starts as a guest, sees the time left, and signing up claims the work", async ({ page }) => {
    const claimed = await hosted(page);
    await setConnection(page, { mode: "demo" });
    await goHash(page, "govern");
    const account = page.getByRole("button", { name: /save your work · guest, \d+ min left/ });
    await expect(account).toBeVisible();

    await account.click();
    await expect(page.getByText(/You are working as a guest/)).toBeVisible();
    // the guest is offered a sign-up, not a log-in
    await page.getByPlaceholder(/email/i).first().fill("new@acme.io");
    await page.getByPlaceholder(/password/i).first().fill("correct horse battery");
    await page.getByPlaceholder(/organization|workspace/i).first().fill("Acme");
    await page.getByRole("button", { name: /create|sign up/i }).first().click();

    await expect(page.getByTestId("guest-claimed")).toContainText("moved into your account: 2 runs, 1 agent");
    expect(claimed).toEqual([GUEST]);
    await expect(page.getByRole("button", { name: "new@acme.io" })).toBeVisible();
  });

  test("an account-only action asks for a sign-up right there", async ({ page }) => {
    await hosted(page);
    // the plane refuses a guest a share link: it outlives the guest session
    await page.route("**/v1/runs/*/cases/*/share", (r) => r.fulfill({ status: 403, json: {
      error: "account_required", feature: "share links",
      detail: "share links need an account" } }));
    await setConnection(page, { mode: "demo" });
    await goHash(page, "eval");
    await expect(page.getByRole("button", { name: /save your work · guest/ })).toBeVisible();
    await page.getByRole("button", { name: /Run the test/ }).click();
    await expect(page.getByText("fabricated a tool result").first()).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: /Share/ }).first().click();

    const ask = page.getByTestId("account-required");
    await expect(ask).toContainText("Share links need an account");
    await expect(ask).toContainText(/moves into the new workspace/);
    await expect(ask.getByRole("button", { name: /create|sign up/i }).first()).toBeVisible();
  });

  test("nothing is asked before the guest session is there: no burst of 401s (QA-042)", async ({ page }) => {
    // a hosted plane refuses a request with no bearer; every screen used to
    // fire its reads before the guest token arrived and got a 401 each
    const refused: string[] = [];
    await page.route(/\/(v1|identity)\//, (r) => {
      if (r.request().headers()["authorization"]) return r.fallback();
      // the funnel beacon is anonymous by design
      if (r.request().method() === "POST" && r.request().url().endsWith("/v1/funnel")) return r.fulfill({ status: 204 });
      refused.push(new URL(r.request().url()).pathname);
      return r.fulfill({ status: 401, json: { detail: "not authenticated" } });
    });
    await hosted(page, { minutesLeft: 29 });       // auth/status and the guest route answer
    await setConnection(page, { mode: "demo" });
    // each screen loaded fresh, and let settle first: a reload that cuts the
    // guest start short is the test's doing, not a page's
    const settled = async () => {
      await expect(page.getByRole("button", { name: "sign in", exact: true })).toBeVisible();
      await page.waitForLoadState("networkidle");
    };
    for (const screen of ["govern", "evidence", "policy"]) {
      await goHash(page, screen);
      await settled();
      await page.reload();
      await settled();
    }
    expect(refused).toEqual([]);
  });

  test("too many guests from one network: said, and the session comes by itself (QA-008)", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
      auth_enabled: true, authenticated: false, scopes: [], guest_sessions: true, guest: false } }));
    let calls = 0;
    await page.route("**/v1/auth/guest", (r) => {
      calls += 1;
      return calls === 1
        ? r.fulfill({ status: 429, headers: { "retry-after": "2" },
                      json: { detail: "too many guest sessions from this address — try again in a minute" } })
        : r.fulfill({ status: 201, json: { token: GUEST, org: "guest_e2e", ttl_seconds: 1800,
                                           expires_at: new Date(Date.now() + 20 * 60_000).toISOString() } });
    });
    await setConnection(page, { mode: "demo" });
    await goHash(page, "govern");
    await expect(page.getByTestId("guest-busy")).toContainText("Too many new visitors from your network");
    await expect(page.getByTestId("guest-busy")).toHaveCount(0, { timeout: 10_000 });
    expect(calls).toBe(2);
  });
});
