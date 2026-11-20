// Settings — notifications, connection, and the EE license check (Settings.tsx).
// The notification subscribe hits the real backend; the license verify rejects
// a bad payload offline.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe("settings", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
  });

  test("subscribes a webhook to a trigger", async ({ page }) => {
    await goHash(page, "settings?tab=alerts");
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await page.getByPlaceholder("https://hooks.example/… (JSON POST)").fill("https://sink.e2e/hook");
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: /Subscribe/ }).click();
    await expect(page.getByRole("button", { name: "subscribed" })).toBeVisible();
  });

  test("a subscribed webhook can be unsubscribed", async ({ page }) => {
    // A registered webhook fired forever: removing one meant editing the
    // database, while the body it receives carries node ids, levels and a
    // permalink.
    await goHash(page, "settings?tab=alerts");
    const url = `https://sink.e2e/drop-${Date.now()}`;
    await page.getByPlaceholder("https://hooks.example/… (JSON POST)").fill(url);
    await page.getByRole("checkbox").first().check();
    await page.getByRole("button", { name: /Subscribe/ }).click();
    await expect(page.getByText(url)).toBeVisible();

    await page.getByRole("button", { name: `unsubscribe ${url}` }).click();
    await expect(page.getByText(url)).toHaveCount(0);
  });

  test("surfaces the dead-letter log (delivery honesty)", async ({ page }) => {
    await goHash(page, "settings?tab=alerts");
    // The failure-honesty log is always surfaced; its contents depend on prior
    // deliveries, so assert the section, not emptiness.
    await expect(page.getByText("DEAD-LETTER LOG")).toBeVisible();
  });

  test("rejects an invalid EE license offline", async ({ page }) => {
    await goHash(page, "settings");
    await page.getByTestId("license-fold").locator("summary").click();
    await page.getByPlaceholder('{"license": {...}, "sig": "…"}').fill('{"license":{},"sig":"deadbeef"}');
    // No vendor-key field: the key a license is checked against is the
    // deployment's (AXOR_VENDOR_PUBKEY), not something this form accepts.
    await expect(page.getByPlaceholder("vendor public key (hex)")).toHaveCount(0);
    await page.getByRole("button", { name: /Verify license/ }).click();
    // A 4xx from the verify endpoint surfaces as a red error line.
    await expect(page.getByText(/^\d{3} \{"detail"/)).toBeVisible();
  });

  test("every trigger the backend emits can be subscribed to", async ({ page }) => {
    // A trigger that fires and cannot be subscribed to is a notification
    // nobody receives — which is how license_expiring and behavioral_drift sat
    // here, emitted by the backend and absent from this list.
    await goHash(page, "settings?tab=alerts");
    for (const label of [
      /its own run contradicts/,
      /a dangerous call went through/,
      /a call the rules would refuse went through/,
      /agents did worse after the team memory changed/,
      /a context health check came out worse/,
      /restricted further/,
      /behaviour drifted/,
      /suspicious across sessions/,
      /stopped reporting/,
      /a policy change breaks a case you kept/,
      /the license is about to expire/,
    ]) {
      await expect(page.getByText(label)).toBeVisible();
    }
  });

  test("the meter and the statement drawn from it are visible", async ({ page }) => {
    // Measured, priced, tested — and reachable only by curl, so a customer
    // could not see their own fleet history or their own bill.
    await goHash(page, "settings?tab=billing");
    const usage = page.getByTestId("usage-billing");
    await expect(usage).toBeVisible();
    await expect(usage.getByText(/A month is billed on its PEAK/)).toBeVisible();
    await expect(usage.getByText("STATEMENT", { exact: false })).toBeVisible();
    // No license on the e2e deployment: the statement says so honestly rather
    // than rendering an empty bill, and usage is still measured.
    // Deliberately not asserting the meter is empty: the suite seeds governed
    // nodes elsewhere, and a test that only passes when it runs first is a test
    // about ordering, not about this panel.
    await expect(usage.getByText(/there is nothing to bill/)).toBeVisible();
  });

  test("the license panel reports the live posture, not just a paste", async ({ page }) => {
    // It rendered only the response to a verify, so a deployment licensed
    // months ago showed nothing until somebody pasted again — and the fields
    // with a deadline attached were not rendered at all.
    await goHash(page, "settings");
    await page.getByTestId("license-fold").locator("summary").click();
    await expect(page.getByText("no license active on this deployment")).toBeVisible();
    await expect(page.getByText(/no auto-renewal/)).toBeVisible();
    await expect(page.getByText(/usage not reported/)).toBeVisible();
  });

  test("tabs follow the journey: a coding team sees no security tab, plain alerts, keys with the lines to use them", async ({ page }) => {
    await page.addInitScript(() => {
      const raw = localStorage.getItem("axor-app");
      const data = raw ? JSON.parse(raw) : { state: {}, version: 0 };
      data.state.journey = "coding";
      data.state.menuAll = false;
      localStorage.setItem("axor-app", JSON.stringify(data));
    });
    await goHash(page, "settings");
    const tabs = page.getByTestId("settings-tabs");
    await expect(tabs.getByRole("tab", { name: "workspace" })).toHaveAttribute("aria-selected", "true");
    await expect(tabs.getByRole("tab", { name: "security" })).toHaveCount(0);
    await tabs.getByRole("tab", { name: "billing" }).click();
    await expect(page.getByTestId("billing-plan")).toContainText("Free");
    await expect(page.getByTestId("developer-usage")).toContainText("developer");
    await expect(page.getByTestId("usage-billing")).toHaveCount(0);
    // on the free plan: what this month would cost, from this workspace's own usage
    await expect(page.getByTestId("if-paid-coding")).toContainText("developers whose agents ran");
    await expect(page.getByTestId("if-paid-security")).toHaveCount(0);
    await tabs.getByRole("tab", { name: "alerts" }).click();
    await expect(page).toHaveURL(/tab=alerts/);
    await expect(page.getByText(/its own run contradicts/)).toBeVisible();
    await expect(page.getByText(/restricted further/)).toHaveCount(0);
    await tabs.getByRole("tab", { name: "connect agents" }).click();
    await expect(page.getByTestId("keys-open")).toContainText("export AXOR_PLANE_URL=");
  });

  test("production sees how the plane is protected, wall by wall", async ({ page }) => {
    await goHash(page, "settings?tab=security");
    const posture = page.getByTestId("security-posture");
    await expect(posture).toContainText("down — fine on your own machine, not for a deployment");
    await expect(posture.getByTestId("posture-auth")).toContainText("set AXOR_API_TOKEN");
    await expect(posture.getByTestId("posture-command_signing")).toContainText("AXOR_OPERATOR_KEYS");
    await expect(page.getByTestId("federation-vault")).toBeVisible();
  });

  test("billing for production: the plan, and what this month would cost on Team and Security", async ({ page }) => {
    await goHash(page, "settings?tab=billing");
    await expect(page.getByTestId("billing-plan")).toContainText("Free");
    await expect(page.getByTestId("if-paid-team").getByTestId("bill-nodes")).toContainText("governed nodes at the peak");
    await expect(page.getByTestId("if-paid-team").getByTestId("bill-total")).toContainText("$");
    await expect(page.getByTestId("if-paid-security")).toBeVisible();
    await expect(page.getByTestId("usage-billing")).toBeVisible();
  });
});
