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

async function hosted(page: Page, opts: { accountRequired?: boolean } = {}) {
  const claimed: string[] = [];
  await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
    auth_enabled: true, authenticated: false, scopes: [], guest_sessions: true, guest: false } }));
  await page.route("**/v1/auth/guest", (r) => r.fulfill({ status: 201, json: {
    token: GUEST, org: "guest_e2e", ttl_seconds: 1800,
    expires_at: new Date(Date.now() + 25 * 60_000).toISOString() } }));
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
    await page.getByRole("button", { name: /Run experiment/ }).click();
    await expect(page.getByText("fabricated a tool result").first()).toBeVisible({ timeout: 20_000 });
    await page.getByRole("button", { name: /Share/ }).first().click();

    const ask = page.getByTestId("account-required");
    await expect(ask).toContainText("Share links need an account");
    await expect(ask).toContainText(/moves into the new workspace/);
    await expect(ask.getByRole("button", { name: /create|sign up/i }).first()).toBeVisible();
  });
});
