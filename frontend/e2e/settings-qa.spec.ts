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

  test("a member or admin is not shown the vault forms only the owner may use (QA-121)", async ({ page }) => {
    // member and admin roles carry read, ingest and operate — not admin
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
      auth_enabled: true, authenticated: true, scopes: ["read", "ingest", "operate"] } }));
    let audit = 0;
    await page.route("**/v1/vault/signing/audit*", (r) => { audit += 1; return r.fulfill({ json: [] }); });
    await goHash(page, "settings?tab=security");
    await expect(page.getByTestId("federation-vault")).toBeVisible();
    await expect(page.getByTestId("vault-owner-only")).toContainText("The workspace owner enrols");
    await expect(page.getByRole("button", { name: /enrol a credential/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /create signing key/ })).toHaveCount(0);
    await expect(page.getByTestId("vault-tokens")).toHaveCount(0);
    // what a member does use is still there: signing their commands
    await expect(page.getByTestId("signing-controls")).toBeVisible();
    expect(audit).toBe(0);
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
    // a paid plan: on the free one a second developer is offered the plan first
    await page.route("**/v1/license/entitlements", (r) => r.fulfill({ json: {
      tier: "coding", free: false, developers: null, agent_families: null, agent_family: null,
      locked: {}, locked_trees: {}, reasons: {} } }));
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
    // QA-321: a sign-in that ended starts no guest session in its place
    await page.waitForTimeout(1500);
    expect(await page.evaluate(() => sessionStorage.getItem("axor-app") ?? "")).not.toContain("gs_e2e_guest_token");
    await expect(page.getByText("○ not signed in")).toBeAttached();
    await expect(page.getByText("● signed in", { exact: true })).toHaveCount(0);
    await expect(page.getByText(/^can: /)).toHaveCount(0);
  });

  test("cosmetics: audit words for vault tokens, an e-mail in key history, \"As an admin\" (QA-070)", async ({ page }) => {
    await page.route("**/v1/audit?*", (r) => r.fulfill({ json: { total: 3, actions: ["vault_token", "vault_token_dropped"], items: [
      { id: "a3", ts: "2026-11-27T10:03:00Z", kind: "control", action: "vault_token_dropped", who: "ada@acme.io", role: "owner", target: "signing", detail: {} },
      { id: "a2", ts: "2026-11-27T10:02:00Z", kind: "control", action: "vault_token", who: "ada@acme.io", role: "owner", target: "signing", detail: { replaced: true } },
      { id: "a1", ts: "2026-11-27T10:01:00Z", kind: "control", action: "vault_token", who: "ada@acme.io", role: "owner", target: "signing", detail: {} },
    ] } }));
    await goHash(page, "settings?tab=audit");
    const rows = page.getByTestId("audit-row");
    await expect(rows.nth(0)).toContainText("dropped the workspace's vault token for");
    await expect(rows.nth(1)).toContainText("made the workspace's vault token for");
    await expect(rows.nth(1)).toContainText("replacing the one before");
    await expect(rows.nth(2)).not.toContainText("replacing");
    await expect(page.getByTestId("audit-action")).not.toContainText("vault_token");

    // key history: the person, not their user id
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
      auth_enabled: true, authenticated: true, scopes: ["read", "ingest", "operate", "admin"] } }));
    await page.route("**/v1/keys", (r) => r.fulfill({ json: [] }));
    await page.route("**/v1/keys/audit?*", (r) => r.fulfill({ json: [{ audit_id: 1, action: "mint",
      ts: "2026-11-27T10:00:00Z", key_id: "k_1", scopes: ["ingest"], node_id: null, label: "x",
      by: { kind: "user", id: "usr_1a2b3c", email: "ada@acme.io" } }] }));
    await goHash(page, "settings?tab=connect");
    const history = page.getByTestId("key-lifecycle-audit");
    await history.locator("summary").click();
    await expect(history).toContainText("by ada@acme.io");
    await expect(history).not.toContainText("usr_1a2b3c");
  });

  test("cosmetics: \"As an admin\" when a viewer is promoted on the free plan (QA-070)", async ({ page }) => {
    await signInAsOwner(page);
    await page.route("**/identity/v1/orgs/org_1/members", (r) => r.fulfill({ json: { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" },
      { user_id: "usr_2", email: "max@acme.io", role: "viewer", since: "2026-01-02" }] } }));
    await page.route("**/identity/v1/orgs/org_1/invites", (r) => r.fulfill({ json: { invites: [] } }));
    await page.route("**/v1/license/entitlements", (r) => r.fulfill({ json: {
      tier: "community", free: true, developers: 1, agent_families: 1, agent_family: null,
      locked: {}, locked_trees: {}, reasons: {} } }));
    await logIn(page);
    await page.getByLabel("role of max@acme.io").selectOption("admin");
    await expect(page.getByTestId("promote-upgrade")).toContainText("As an admin, max@acme.io");
  });

  test("cosmetics: the invitation page shows no guest \"too many visitors\" notice (QA-070)", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
      auth_enabled: true, authenticated: false, guest_sessions: true, scopes: [] } }));
    await page.route("**/v1/auth/guest", (r) => r.fulfill({ status: 429, headers: { "retry-after": "60" },
      json: { detail: "too many guests from this address" } }));
    await page.route("**/identity/v1/invites/tok-busy", (r) => r.fulfill({ json: {
      org_name: "Acme", role: "member", for_email: null, expires_ts: "2026-12-04T12:00:00Z" } }));
    await goHash(page, "invite/tok-busy");
    await expect(page.getByTestId("invite")).toContainText("Acme");
    await page.waitForTimeout(500);
    await expect(page.getByTestId("guest-busy")).toHaveCount(0);
    await goHash(page, "home");
    await expect(page.getByTestId("guest-busy")).toBeVisible();
  });

  test("cosmetics: at 390px the signing-key fields are not cut off (QA-070)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await goHash(page, "settings?tab=security");
    const controls = page.getByTestId("signing-controls");
    await expect(controls).toBeVisible();
    expect((await controls.getByLabel("signing key operators").boundingBox())!.width).toBeGreaterThan(150);
    const fits = (label: string) => controls.getByLabel(label).evaluate((el) => {
      const input = el as HTMLInputElement;
      const cs = getComputedStyle(input);
      const ctx = document.createElement("canvas").getContext("2d")!;
      ctx.font = `${cs.fontSize} ${cs.fontFamily}`;
      const room = input.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
      return ctx.measureText(input.placeholder).width <= room;
    });
    expect(await fits("vault signing-token")).toBe(true);
    expect(await fits("signing key operators")).toBe(true);
  });

  test("cosmetics: the sign-up note covers nothing, the header's connect among them (QA-070)", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: { auth_enabled: true, authenticated: false, scopes: [] } }));
    await page.route("**/identity/v1/signup-mode", (r) => r.fulfill({ json: { mode: "open", signup: true } }));
    await page.route("**/identity/v1/signup", (r) => r.fulfill({ status: 201, json: SESSION }));
    await setConnection(page, { mode: "none" });
    await goHash(page, "home");
    await page.getByRole("button", { name: "sign in", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "sign in" });
    await dialog.getByRole("button", { name: "new? create a workspace" }).click();
    await dialog.getByLabel("email").fill("ada@acme.io");
    await dialog.getByLabel("password").fill("correct horse");
    await dialog.getByLabel("organization name").fill("Acme");
    await dialog.getByRole("button", { name: "create workspace" }).click();
    const note = page.getByTestId("signup-done");
    await expect(note).toBeVisible();
    await expect(page).toHaveURL(/#\/connect/);
    const box = (await note.boundingBox())!;
    for (const b of await page.locator("button:visible").all()) {
      if (await note.locator("button").evaluate((own, other) => own === other, await b.elementHandle())) continue;
      const o = await b.boundingBox();
      if (!o) continue;
      const overlap = o.x < box.x + box.width && box.x < o.x + o.width && o.y < box.y + box.height && box.y < o.y + o.height;
      expect(overlap, `the note covers "${await b.innerText()}"`).toBe(false);
    }
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

// QA-323: a pasted token the plane does not take was kept and sent with every
// request, and the page only showed "○ not signed in"
test("a bogus access token is said to be refused, and can be cleared", async ({ page }) => {
  await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
    auth_enabled: true, authenticated: false, guest_sessions: false, scopes: [] } }));
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "settings?tab=workspace");
  await page.getByText("use an access token instead").click();
  await page.getByLabel("access token").fill("not-a-real-token");
  await expect(page.getByTestId("token-refused")).toContainText("the plane did not accept this token");
  await page.getByTestId("token-refused").getByRole("button", { name: "clear" }).click();
  await expect(page.getByLabel("access token")).toHaveValue("");
  await expect(page.getByTestId("token-refused")).toHaveCount(0);
});

// QA-301: a signed-out visitor on the hosted plane was shown the self-hosted
// license hint — the license status answered 401, read as "not hosted"
test("the license-file hint is for a plane that said it is self-hosted, not for an unknown one", async ({ page }) => {
  let status: { code: number; body: unknown } = { code: 401, body: { error: "unauthorized" } };
  await page.route("**/v1/license/status", (r) => r.fulfill({ status: status.code, json: status.body }));
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "settings?tab=billing");
  await expect(page.getByText(/billing|plan/i).first()).toBeVisible();
  await page.waitForTimeout(800);
  await expect(page.getByTestId("billing-license-hint")).toHaveCount(0);
  status = { code: 200, body: { active: false, vendor_key_configured: true, hosted: false } };
  await page.reload();
  await expect(page.getByTestId("billing-license-hint")).toContainText("the license file you were sent");
});

// QA-306: plan changes showed as raw event names on the trail
test("plan changes on the audit trail are in words", async ({ page }) => {
  await setConnection(page, { mode: "adapter" });
  await page.route("**/v1/audit?*", (r) => r.fulfill({ json: { total: 3,
    actions: ["plan_change_requested", "plan_upgraded", "plan_downgraded"], items: [
      { id: "a3", ts: "2026-11-28T10:03:00Z", kind: "control", action: "plan_downgraded", who: "license renewal", target: "coding", detail: { tier: "coding", until: "2026-12-31" } },
      { id: "a2", ts: "2026-11-28T10:02:00Z", kind: "control", action: "plan_upgraded", who: "license renewal", target: "team", detail: { tier: "team" } },
      { id: "a1", ts: "2026-11-28T10:01:00Z", kind: "control", action: "plan_change_requested", who: "ada@acme.io", role: "owner", target: "team", detail: {} },
    ] } }));
  await goHash(page, "settings?tab=audit");
  const rows = page.getByTestId("audit-row");
  await expect(rows.nth(0)).toContainText("moved the plan down to");
  await expect(rows.nth(1)).toContainText("moved the plan up to");
  await expect(rows.nth(2)).toContainText("asked to change the plan to");
  await expect(page.getByText(/plan_(change_requested|upgraded|downgraded)/)).toHaveCount(0);
});

// QA-303: the month table read every month on today's plan — the running month
// recomputed on it minutes after a switch, and past months too
test("each month shows the plan it was billed on; the running month says it is an estimate", async ({ page }) => {
  const month = (m: string, tier: string, total: string) => ({ month: m, workspace_tier: tier, total, priced: true,
    active_developers: 1, peak_nodes: 0, developers: [], base: "$0.00", base_cents: 0, included_nodes: 1,
    peak_day: null, billable_nodes: 0, overage: "$0.00", overage_cents: 0, included_developers: 0,
    billable_developers: 1, seats: total, seat_cents: 0, total_cents: 0, provisional: false, note: "", computed_on: "" });
  await page.route("**/v1/license/billing", (r) => r.fulfill({ json: {
    plan: { tier: "coding", licensed: true, organization: "org_acme", expires_at: "2026-12-31", base: null,
            base_cents: null, per_node_cents: 7500, seat: "$25.00", seat_cents: 2500, included_nodes: 1,
            included_developers: 0, priced: true },
    current: month("2026-11", "coding", "$25.00"), history: [month("2026-10", "team", "$299.00")], if_paid: [],
    forecast: null } }));
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "settings?tab=billing");
  await expect(page.getByTestId("billing-month-plan-2026-10")).toHaveText("Team");
  await expect(page.getByTestId("billing-month-plan-2026-11")).toHaveText("estimated on Coding");
  await expect(page.getByTestId("billing-estimate-note")).toContainText("an estimate on the plan in force now, Coding");
});
