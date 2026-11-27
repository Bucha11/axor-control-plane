// Settings, identity and alerts: what exploratory QA found on 0.7.2 (QA-0xx
// in each test). Stubbed where the shared test plane cannot take the state.
import { expect, test, type Page } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

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
});
