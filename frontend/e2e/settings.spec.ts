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
    await page.getByPlaceholder(/https:\/\/hooks.example\/…/).fill("https://sink.e2e/hook");
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
    await page.getByPlaceholder(/https:\/\/hooks.example\/…/).fill(url);
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
    await expect(page.getByText("ALERTS THAT COULD NOT BE DELIVERED")).toBeVisible();
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
    ]) {
      await expect(page.getByLabel(label)).toBeVisible();
    }
    // a license that expires is a licensed workspace's news: this one has none
    await expect(page.getByLabel(/the license is about to expire/)).toHaveCount(0);
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
    await expect(page.getByLabel(/its own run contradicts/)).toBeVisible();
    await expect(page.getByLabel(/restricted further/)).toHaveCount(0);
    await tabs.getByRole("tab", { name: "connect agents" }).click();
    await expect(page.getByTestId("keys-open")).toContainText("axor-wrap connect http");
  });

  test("a member makes a key for their own machine — not only the owner", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ auth_enabled: true, authenticated: true, scopes: ["read", "ingest"] }) }));
    let made = false;
    await page.route("**/v1/keys/mine", (r) => {
      if (r.request().method() === "POST") {
        made = true;
        return r.fulfill({ status: 201, contentType: "application/json",
          body: JSON.stringify({ key_id: "k_dev2", secret: "ak_dev2secret", scopes: ["ingest"], node_id: null }) });
      }
      return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(made
        ? [{ key_id: "k_dev2", label: "dev2@acme.io · coding agents", created_ts: "2026-11-21T18:00:00Z", scopes: ["ingest"] }]
        : []) });
    });
    await goHash(page, "settings?tab=connect");
    const mine = page.getByTestId("my-keys");
    await expect(mine).toContainText("A key for your machine");
    await page.getByTestId("my-key-create").click();
    await expect(page.getByTestId("my-key-connect")).toContainText("axor-wrap connect http");
    await expect(page.getByTestId("my-key-connect")).toContainText("--key ak_dev2secret");
    await expect(mine).toContainText("k_dev2");
  });

  test("the owner changes what a key may do, in place", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ auth_enabled: true, authenticated: true, scopes: ["read", "ingest", "operate", "admin"] }) }));
    let scopes = ["ingest"];
    let put: unknown = null;
    await page.route("**/v1/keys", (r) => r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify([{ key_id: "k_dev2", scopes, label: "dev2@acme.io · coding agents",
                              created_ts: "2026-11-21T18:00:00Z", node_id: null }]) }));
    await page.route("**/v1/keys/k_dev2", async (r) => {
      put = r.request().postDataJSON();
      scopes = (put as { scopes: string[] }).scopes;
      await r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ key_id: "k_dev2", scopes }) });
    });
    await goHash(page, "settings?tab=connect");
    const role = page.getByLabel("role of k_dev2");
    await expect(role).toHaveValue("agent");
    await role.selectOption("agent+read");
    await expect(role).toHaveValue("agent+read");
    expect(put).toEqual({ scopes: ["read", "ingest"] });
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

test.describe("developer seats on a paid plan", () => {
  const st = (developers: object[], extra: object = {}) => ({
    month: "2026-11", workspace_tier: "coding", base: "$0.00", base_cents: 0, included_nodes: 1,
    peak_nodes: 0, peak_day: null, billable_nodes: 0, overage: "$0.00", overage_cents: 0,
    included_developers: 0, active_developers: 2, billable_developers: 2, seats: "$50.00",
    seat_cents: 5000, total: "$50.00", total_cents: 5000, provisional: true, priced: true,
    note: "", computed_on: "2026-11-23", developers, ...extra,
  });

  test("this month's seats, where the bill is heading, and a limit an admin sets", async ({ page }) => {
    await page.addInitScript(() => {
      const raw = localStorage.getItem("axor-app");
      const data = raw ? JSON.parse(raw) : { state: {}, version: 0 };
      data.state.journey = "coding";
      localStorage.setItem("axor-app", JSON.stringify(data));
    });
    let cap: number | null = null;
    const put: unknown[] = [];
    await page.route("**/v1/license/seat-cap", async (route) => {
      const body = route.request().postDataJSON() as { cap: number | null };
      put.push(body);
      cap = body.cap;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ seat_cap: cap }) });
    });
    await page.route("**/v1/license/billing", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({
        plan: { tier: "coding", licensed: true, organization: "Acme", expires_at: null, base: "$0.00",
                base_cents: 0, per_node_cents: 0, seat: "$25.00", seat_cents: 2500, included_nodes: 1,
                included_developers: 0, priced: true },
        current: st([
          { developer: "ada@acme.io", sessions: 4, agents: ["claude-code"], first_day: "2026-11-02", seated: true },
          { developer: "bo@acme.io", sessions: 1, agents: ["codex"], first_day: "2026-11-09", seated: true },
        ], { seat_cap: cap, over_cap: 0 }),
        history: [], if_paid: [],
        forecast: { developers_so_far: 2, likely: [{ developer: "cy@acme.io", last_day: "2026-10-30" }],
                    projected_developers: cap ? Math.min(cap, 3) : 3,
                    projected_total_cents: 7500, projected_total: cap === 2 ? "$50.00" : "$75.00",
                    seat_cap: cap, seats_left: cap === null ? null : cap - 2, next_seat_cents: 2500 },
      }) }));
    await goHash(page, "settings?tab=billing");
    const seats = page.getByTestId("billing-seats");
    await expect(seats.getByTestId("seats-so-far")).toContainText("2 developers so far · $50.00");
    await expect(seats.getByTestId("seats-forecast")).toContainText("3 developers → $75.00");
    await expect(seats.getByTestId("seats-forecast")).toContainText("cy@acme.io ran agents in the last 30 days");
    await expect(seats.getByTestId("seats-list")).toContainText("ada@acme.io");
    await seats.getByTestId("seat-cap-edit").click();
    await seats.getByTestId("seat-cap-input").fill("2");
    await expect(seats.getByTestId("seat-cap")).toContainText("at most $25.00 × 2 = $50.00 a month");
    await seats.getByTestId("seat-cap-save").click();
    await expect(seats.getByTestId("seat-cap")).toContainText("2 · 0 left");
    await expect(seats.getByTestId("seats-forecast")).toContainText("2 developers → $50.00");
    expect(put).toEqual([{ cap: 2 }]);
  });
});
