// What a role may do, as the client shows it, and how it treats a refusal:
// findings of the journaled QA run on 0.7.3 (QA-NNN in each test). The role is
// played by stubbing /v1/auth/status's scopes.
import { expect, test, type Page } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

const VIEWER = ["read"];
const MEMBER = ["read", "ingest", "operate"];

async function playRole(page: Page, scopes: string[]): Promise<void> {
  await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
    auth_enabled: true, authenticated: true, scopes } }));
}

// a signed-in tab: its credentials live in this tab's session storage
async function signedIn(page: Page, role = "member"): Promise<void> {
  await page.addInitScript(() => {
    window.sessionStorage.setItem("axor-app", JSON.stringify({ version: 0, state: {
      apiToken: "acc-1", refreshToken: "ref-1", identityEmail: "ada@acme.io",
      vaultSigningToken: "", vaultCredsToken: "", guestTokens: [] } }));
  });
  await page.route("**/identity/v1/me", (r) => r.fulfill({ json: {
    user: { user_id: "u1", email: "ada@acme.io" }, active_org: "org_1", role,
    memberships: [{ org_id: "org_1", role, name: "Acme", tier: "free" }] } }));
  await page.route("**/identity/v1/orgs/org_1/members", (r) => r.fulfill({ json: { members: [
    { user_id: "u1", email: "ada@acme.io", role, since: "2026-11-01T00:00:00Z" },
    { user_id: "u0", email: "owner@acme.io", role: "owner", since: "2026-11-01T00:00:00Z" }] } }));
}

const ONE_INCIDENT = {
  items: [{ id: "ev:r1:0", kind: "dangerous", ts: new Date().toISOString(), node_id: "agent-1", run_id: "r1",
            seq: null, what: "send_email", detail: "", count: 1, status: "new" }],
  new: { made_up: 0, dangerous: 1, would_refuse: 0, refused: 0 }, needs_a_look: 1,
};

test.describe("access (QA, 0.7.3)", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
  });

  test("a scope refusal is one request, not a refresh and a resend, and says who can (QA-195, QA-019)", async ({ page }) => {
    // the page thinks it may (a member), the plane says the role is a viewer's now
    await playRole(page, MEMBER);
    await signedIn(page);
    await page.route("**/v1/incidents?*", (r) => r.fulfill({ json: ONE_INCIDENT }));
    let puts = 0;
    let refreshes = 0;
    await page.route("**/v1/incidents/status", (r) => {
      puts += 1;
      return r.fulfill({ status: 403, json: { error: "forbidden", need: "operate", have: ["read"] } });
    });
    await page.route("**/identity/v1/refresh", (r) => {
      refreshes += 1;
      return r.fulfill({ json: { access_token: "acc-2", refresh_token: "ref-2", user: { email: "ada@acme.io" } } });
    });
    await goHash(page, "evidence");
    await page.getByTestId("incident-status").selectOption("looked_at");
    await expect(page.getByTestId("incident-status-error"))
      .toHaveText("a viewer can't change this — an owner, admin or member can");
    await page.waitForTimeout(500);
    expect(puts).toBe(1);
    expect(refreshes).toBe(0);
  });
});
