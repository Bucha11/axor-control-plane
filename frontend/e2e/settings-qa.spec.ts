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
});
