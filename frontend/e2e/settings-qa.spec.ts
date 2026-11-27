// Settings, identity and alerts: what exploratory QA found on 0.7.2 (QA-0xx
// in each test). Stubbed where the shared test plane cannot take the state.
import { expect, test, type Page } from "@playwright/test";
import { answerAsk, goHash, setConnection } from "./helpers";

const pageWidth = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth);

test.describe("settings (QA, 0.7.2)", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
  });

  test("a long webhook address wraps on alerts and on the audit trail (QA-062)", async ({ page }) => {
    // a 300-character address stretched the page to 2732px and the audit tab to 1792px
    await page.setViewportSize({ width: 1366, height: 900 });
    await goHash(page, "settings?tab=alerts");
    const url = `https://sink.e2e/${"x".repeat(280)}${Date.now()}`;
    await page.getByPlaceholder(/https:\/\/hooks.example\/…/).fill(url);
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: /Subscribe/ }).click();
    await expect(page.getByRole("button", { name: `unsubscribe ${url}` })).toBeVisible();
    expect(await pageWidth(page)).toBeLessThanOrEqual(1366);
    await page.getByTestId("settings-tab-audit").click();
    await expect(page.getByTestId("audit-row").first()).toBeVisible();
    expect(await pageWidth(page)).toBeLessThanOrEqual(1366);
    await page.getByTestId("settings-tab-alerts").click();
    await page.getByRole("button", { name: `unsubscribe ${url}` }).click();
  });

  test("a hosted plane's tool credentials card says they must be sealed, not PLAINTEXT (QA-060)", async ({ page }) => {
    await page.route("**/v1/security/posture", (r) => r.fulfill({ json: { items: [
      { id: "auth", ok: true, what: "every request needs a token or a sign-in", fix: "" },
      { id: "sealed_credentials", ok: true, fix: "",
        what: "tool credentials are kept only sealed to the workspace's own key: this plane cannot read them" },
    ] } }));
    await page.route("**/v1/vault/creds/sealing-key", (r) => r.fulfill({ json: { public_key_hex: null, envelope_mode: false } }));
    await goHash(page, "settings?tab=security");
    const card = page.getByTestId("tool-credentials");
    await expect(card.getByTestId("sealed-only")).toContainText("a plaintext one is refused");
    await expect(card).not.toContainText("PLAINTEXT");
    await card.getByRole("button", { name: "enrol a credential" }).click();
    await expect(card.getByTestId("enrol-form")).toContainText("refused until a sealing key is registered");
    await expect(card.getByTestId("enrol-form")).not.toContainText("store it as-is");
  });

  test("a long key label wraps; the role and revoke stay on screen (QA-063)", async ({ page }) => {
    // a ~180-character label made the page 1540px wide on a 1366px screen
    await page.setViewportSize({ width: 1366, height: 900 });
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
      auth_enabled: true, authenticated: true, scopes: ["read", "ingest", "operate", "admin"] } }));
    await page.route("**/v1/keys", (r) => r.fulfill({ json: [{ key_id: "k_long", scopes: ["ingest"],
      label: "L".repeat(180), created_ts: "2026-11-21T18:00:00Z", node_id: null }] }));
    await goHash(page, "settings?tab=connect");
    await expect(page.getByLabel("role of k_long")).toBeVisible();
    expect(await pageWidth(page)).toBeLessThanOrEqual(1366);
    const revoke = (await page.getByRole("button", { name: "revoke k_long" }).boundingBox())!;
    expect(revoke.x + revoke.width).toBeLessThanOrEqual(1366);
    expect((await page.getByLabel("role of k_long").boundingBox())!.width).toBeGreaterThan(100);
  });

  test("a revoked key's shown-once command is cleared (QA-066)", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
      auth_enabled: true, authenticated: true, scopes: ["read", "ingest", "operate", "admin"] } }));
    let keys: { key_id: string; scopes: string[]; label: string; created_ts: string; node_id: null }[] = [];
    await page.route("**/v1/keys", (r) => {
      if (r.request().method() === "POST") {
        keys = [{ key_id: "k_gone", scopes: ["ingest"], label: "x", created_ts: "2026-11-21T18:00:00Z", node_id: null }];
        return r.fulfill({ status: 201, json: { key_id: "k_gone", secret: "ak_gonesecret", scopes: ["ingest"], node_id: null } });
      }
      return r.fulfill({ json: keys });
    });
    await page.route("**/v1/keys/k_gone", (r) => { keys = []; return r.fulfill({ json: { revoked: "k_gone" } }); });
    await goHash(page, "settings?tab=connect");
    await page.getByTestId("key-mint").click();
    await expect(page.getByTestId("key-minted")).toContainText("ak_gonesecret");
    await page.getByRole("button", { name: "revoke k_gone" }).click();
    await answerAsk(page);
    await expect(page.getByTestId("key-minted")).toHaveCount(0);
  });

  test("a viewer is not offered what the plane refuses them, and is told who can (QA-064)", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
      auth_enabled: true, authenticated: true, scopes: ["read"] } }));
    await page.route("**/v1/notifications/subscriptions", (r) => r.fulfill({ json: [
      { url: "https://hooks.example/team", triggers: ["node_stale"], label: "", node_pattern: "*", debounce_seconds: 0 }] }));
    await goHash(page, "settings?tab=connect");
    await expect(page.getByTestId("keys-viewer")).toContainText("an owner or admin can make you a member");
    await expect(page.getByTestId("my-key-create")).toHaveCount(0);
    await page.getByTestId("settings-tab-alerts").click();
    await expect(page.getByTestId("alerts-viewer")).toContainText("an owner, admin or member subscribes");
    await expect(page.getByText("https://hooks.example/team")).toBeVisible();
    await expect(page.getByRole("button", { name: /Subscribe/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /send a test alert/ })).toHaveCount(0);
    await page.getByTestId("settings-tab-security").click();
    await expect(page.getByTestId("vault-viewer")).toBeVisible();
    await expect(page.getByTestId("federation-vault")).toHaveCount(0);
  });

  test("vault token replace and drop ask first; dropping one keeps the other's shown value (QA-065)", async ({ page }) => {
    // stubbed: a real token on the shared test plane would lock the vault for other specs
    const state = { creds: false, signing: true };
    const row = (set: boolean) => ({ set, since: set ? "2026-11-27T10:00:00+00:00" : null, by: set ? "ada@acme.io" : null, deployment: false });
    await page.route("**/v1/vault/tokens", (r) => r.fulfill({ json: { creds: row(state.creds), signing: row(state.signing) } }));
    const calls: string[] = [];
    await page.route("**/v1/vault/tokens/*", (r) => {
      const which = r.request().url().endsWith("creds") ? "creds" : "signing";
      calls.push(`${r.request().method()} ${which}`);
      if (r.request().method() === "DELETE") { state[which] = false; return r.fulfill({ json: { set: false } }); }
      state[which] = true;
      return r.fulfill({ status: 201, json: { subsystem: which, token: `axv_${which}_new`, header: "X-Vault-Token" } });
    });
    await goHash(page, "settings?tab=security");
    await page.getByTestId("vault-token-creds-make").click();
    await expect(page.getByTestId("vault-token-shown")).toContainText("axv_creds_new");
    // replace: asked, and cancelling sends nothing
    await page.getByTestId("vault-token-signing-replace").click();
    await expect(page.getByTestId("ask")).toContainText("Replace the token");
    await page.getByTestId("ask").getByRole("button", { name: "cancel" }).click();
    // drop: asked; the creds token just shown stays on screen
    await page.getByTestId("vault-token-signing-drop").click();
    await expect(page.getByTestId("ask")).toContainText("Drop the token");
    await answerAsk(page);
    await expect(page.getByTestId("vault-token-signing-make")).toBeVisible();
    await expect(page.getByTestId("vault-token-shown")).toContainText("axv_creds_new");
    expect(calls).toEqual(["POST creds", "DELETE signing"]);
  });

  test("the owner's team list picks up an accepted invite without a reload (QA-067)", async ({ page }) => {
    await signInAsOwner(page);
    let accepted = false;
    await page.route("**/identity/v1/orgs/org_1/members", (r) => r.fulfill({ json: { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" },
      ...(accepted ? [{ user_id: "usr_2", email: "bob@acme.io", role: "member", since: "2026-11-27" }] : [])] } }));
    await page.route("**/identity/v1/orgs/org_1/invites", (r) => r.fulfill({ json: { invites: accepted ? [] : [
      { invite_id: "inv_1", role: "member", email: "bob@acme.io", created_ts: "2026-11-27T10:00:00Z",
        expires_ts: "2026-12-04T10:00:00Z" }] } }));
    await logIn(page);
    await expect(page.getByTestId("invites-open")).toContainText("bob@acme.io");
    accepted = true;
    // back to the tab: asked again (and every 15 s while an invite waits)
    await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
    await expect(page.getByTestId("member-bob@acme.io")).toBeVisible();
    await expect(page.getByTestId("invites-open")).toHaveCount(0);
  });

  test("an invite error goes once its fields change (QA-068)", async ({ page }) => {
    await signInAsOwner(page);
    await page.route("**/identity/v1/orgs/org_1/members", (r) => r.fulfill({ json: { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" }] } }));
    await page.route("**/identity/v1/orgs/org_1/invites", (r) => r.request().method() === "POST"
      ? r.fulfill({ status: 422, json: { detail: "that is not an e-mail address" } })
      : r.fulfill({ json: { invites: [] } }));
    await logIn(page);
    await page.getByLabel("invite email").fill("bob@acme.io");
    await page.getByTestId("invite-make").click();
    await expect(page.getByText("that is not an e-mail address")).toBeVisible();
    await page.getByLabel("invite role").selectOption("viewer");
    await expect(page.getByText("that is not an e-mail address")).toHaveCount(0);
    await page.getByTestId("invite-make").click();
    await expect(page.getByText("that is not an e-mail address")).toBeVisible();
    await page.getByLabel("invite email").fill("");
    await expect(page.getByText("that is not an e-mail address")).toHaveCount(0);
  });

  test("a double-click on send a test alert, or on resend link, sends once (QA-069)", async ({ page }) => {
    await signInAsOwner(page);
    await page.route("**/identity/v1/me", (r) => r.fulfill({ json: {
      user: { ...SESSION.user, email_verified: false }, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }] } }));
    await page.route("**/identity/v1/email", (r) => r.fulfill({ json: { available: true } }));
    let resends = 0;
    await page.route("**/identity/v1/email/verify/send", async (r) => {
      resends += 1;
      await new Promise((ok) => setTimeout(ok, 300));
      await r.fulfill({ json: { sent_to: "ada@acme.io" } });
    });
    await page.route("**/v1/notifications/subscriptions", (r) => r.fulfill({ json: [
      { url: "https://hooks.example/once", triggers: ["node_stale"], label: "", node_pattern: "*", debounce_seconds: 0 }] }));
    let tests = 0;
    await page.route("**/v1/notifications/test", async (r) => {
      tests += 1;
      await new Promise((ok) => setTimeout(ok, 300));
      await r.fulfill({ json: { sent: "https://hooks.example/once" } });
    });
    await logIn(page);
    await page.getByTestId("settings-tab-alerts").click();
    await page.getByRole("button", { name: "send a test alert to https://hooks.example/once" }).dblclick();
    await expect(page.getByText("test sent ✓")).toBeVisible();
    expect(tests).toBe(1);
    const status = page.getByTestId("email-unverified");
    await status.getByRole("button", { name: "resend link" }).dblclick();
    await expect(status).toContainText("link sent to ada@acme.io");
    expect(resends).toBe(1);
  });

  test("a guest's Settings asks for nothing a guest cannot have; after log-in it asks with the new token (QA-049)", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => {
      const bearer = r.request().headers()["authorization"] ?? "";
      return r.fulfill({ json: bearer.includes("gs_")
        ? { auth_enabled: true, authenticated: true, guest: true, guest_sessions: true, scopes: ["read", "ingest", "operate"] }
        : bearer.includes("acc-token")
          ? { auth_enabled: true, authenticated: true, guest: false, scopes: ["read", "ingest", "operate", "admin"] }
          : { auth_enabled: true, authenticated: false, guest_sessions: true, scopes: [] } });
    });
    await page.route("**/v1/auth/guest", (r) => r.fulfill({ status: 201, json: {
      token: "gs_e2e_guest_token", org: "guest_e2e", ttl_seconds: 1800,
      expires_at: new Date(Date.now() + 29 * 60_000).toISOString() } }));
    await page.route("**/v1/auth/claim-guest", (r) => r.fulfill({ json: { moved: {} } }));
    await signInAsOwner(page);
    const asGuest: string[] = [];
    const license: string[] = [];
    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      const bearer = r.headers()["authorization"] ?? "";
      if (/^\/v1\/(keys|notifications|license|vault)/.test(path) && path !== "/v1/license/entitlements"
          && bearer.includes("gs_")) asGuest.push(path);
      if (path === "/v1/license/status") license.push(bearer);
    });
    await goHash(page, "settings?tab=connect");
    await page.waitForFunction(() => (sessionStorage.getItem("axor-app") ?? "").includes("gs_e2e_guest_token"));
    await expect(page.getByTestId("connections")).toBeVisible();
    await expect(page.getByText("Sign in to make a key for your machine.")).toBeVisible();
    await page.getByTestId("settings-tab-alerts").click();
    await expect(page.getByText("ALERTS THAT COULD NOT BE DELIVERED")).toBeVisible();
    await page.getByTestId("settings-tab-workspace").click();
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();
    await expect.poll(() => license).toContain("Bearer acc-token");
    expect(asGuest).toEqual([]);
    expect(license.filter((b) => b.includes("gs_"))).toEqual([]);
  });

  test("a refused token refresh says the session ended, and Settings shows one state (QA-102)", async ({ page }) => {
    // a signed-in tab whose refresh token another tab already rotated
    await page.addInitScript(() => {
      if (sessionStorage.getItem("qa102")) return;
      sessionStorage.setItem("qa102", "1");
      sessionStorage.setItem("axor-app", JSON.stringify({ version: 0, state: {
        apiToken: "acc-old", refreshToken: "ref-old", identityEmail: "ada@acme.io",
        vaultSigningToken: "", vaultCredsToken: "", guestTokens: [] } }));
    });
    await page.route("**/v1/auth/status", (r) => {
      const bearer = r.request().headers()["authorization"] ?? "";
      return r.fulfill({ json: bearer.includes("gs_")
        ? { auth_enabled: true, authenticated: true, guest: true, guest_sessions: true, scopes: ["read", "ingest", "operate"] }
        : { auth_enabled: true, authenticated: false, guest_sessions: true, scopes: [] } });
    });
    await page.route("**/v1/auth/guest", (r) => r.fulfill({ status: 201, json: {
      token: "gs_e2e_guest_token", org: "guest_e2e", ttl_seconds: 1800,
      expires_at: new Date(Date.now() + 29 * 60_000).toISOString() } }));
    await page.route("**/identity/v1/me", (r) => r.fulfill({ status: 401, json: { detail: "token expired" } }));
    await page.route("**/identity/v1/refresh", (r) => r.fulfill({ status: 401, json: { detail: "invalid refresh token" } }));
    await goHash(page, "settings");
    await expect(page.getByTestId("session-ended")).toContainText("your session ended — sign in again");
    await expect(page.getByRole("button", { name: "log in", exact: true })).toBeVisible();
    await page.waitForFunction(() => (sessionStorage.getItem("axor-app") ?? "").includes("gs_e2e_guest_token"));
    await expect(page.getByText("○ not signed in")).toBeAttached();
    await expect(page.getByText("● signed in", { exact: true })).toHaveCount(0);
    await expect(page.getByText(/^can: /)).toHaveCount(0);
  });
});

const SESSION = {
  access_token: "acc-token", refresh_token: "ref-token", token_type: "Bearer", expires_in: 900,
  user: { user_id: "usr_1", email: "ada@acme.io" },
  org: { org_id: "org_1", role: "owner", tier: "community" },
};

// the identity service is not in the e2e stack: stubbed
async function signInAsOwner(page: Page): Promise<void> {
  await page.route("**/identity/v1/login", (r) => r.fulfill({ json: SESSION }));
  await page.route("**/identity/v1/me", (r) => r.fulfill({ json: {
    user: SESSION.user, active_org: "org_1", role: "owner",
    memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }] } }));
}

async function logIn(page: Page): Promise<void> {
  await goHash(page, "settings");
  await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
  await page.getByLabel("password").fill("correct horse");
  await page.getByRole("button", { name: "log in", exact: true }).click();
  await expect(page.getByText("workspace Acme")).toBeVisible();
}
