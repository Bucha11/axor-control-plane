// The Enterprise rung's section of the audit tab: a period of the whole
// governed surface as one document, and the legal hold that keeps its
// evidence alive. The e2e stack carries no license, so the plane's answers are
// stubbed here — this checks the page, not the pack (packages/axor-backend/
// tests/test_compliance.py checks what is in it).
import { expect, test } from "@playwright/test";
import { answerAsk, goHash } from "./helpers";

const REPORT = {
  month: "2026-11", acts: 0, by_action: {}, by_person: {}, on_others_sessions: [],
  policy_changes: [], permission_changes: [], brakes: [], keys: [], access_changes: [],
};

test.describe("compliance pack and legal hold (Enterprise)", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/v1/audit?*", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ items: [], total: 0, actions: [] }),   // no window: Business+
    }));
    await page.route("**/v1/audit/retention", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({ days: null }) }));
    await page.route("**/v1/audit/report*", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify(REPORT) }));
  });

  test("the pack is asked for by period, and a hold freezes the trail", async ({ page }) => {
    await page.route("**/v1/license/entitlements", async (route) => {
      const body = await (await route.fetch()).json();
      await route.fulfill({ json: { ...body, tier: "enterprise", free: false } });
    });
    let held: unknown[] = [];
    let asked = "";
    await page.route("**/v1/audit/holds", async (route) => {
      if (route.request().method() === "POST") {
        const { reason } = route.request().postDataJSON();
        held = [{ hold_id: "hold_1", reason, placed_by: "ada@acme.io",
                  placed_ts: "2026-11-23T10:00:00Z", released_ts: null, released_by: null }];
        await route.fulfill({ status: 201, contentType: "application/json",
                              body: JSON.stringify(held[0]) });
        return;
      }
      await route.fulfill({ status: 200, contentType: "application/json",
                            body: JSON.stringify({ holds: held }) });
    });
    await page.route("**/v1/audit/compliance*", async (route) => {
      asked = new URL(route.request().url()).search;
      await route.fulfill({ status: 200, contentType: "application/json",
                            headers: { "content-disposition": 'attachment; filename="pack.json"' },
                            body: '{"schema":"axor.compliance/1"}' });
    });
    await goHash(page, "settings?tab=audit");
    const box = page.getByTestId("audit-reviewer");
    // the period defaults to the quarter just ended
    const from = await box.getByTestId("pack-from").inputValue();
    const to = await box.getByTestId("pack-to").inputValue();
    expect(from).toMatch(/-(01|04|07|10)-01$/);
    expect(from < to).toBe(true);
    await box.getByTestId("pack-json").click();
    await expect.poll(() => asked).toContain(`from=${from}`);
    expect(asked).toContain("format=json");

    // no hold: retention prunes on its schedule
    await expect(box.getByTestId("audit-holds")).toContainText("none");
    await box.getByTestId("hold-place").click();
    await answerAsk(page, "Acme v. Roe, discovery");
    await expect(box.getByTestId("audit-holds")).toContainText("Acme v. Roe, discovery");
    await expect(box.getByTestId("audit-holds")).toContainText("nothing is pruned");
  });

  test("below Enterprise the section is not there at all", async ({ page }) => {
    await page.route("**/v1/audit/holds", (route) => route.fulfill({
      status: 402, contentType: "application/json",
      body: JSON.stringify({ detail: { error: "plan_required", plan: "enterprise",
                                       detail: "the compliance pack is on the Enterprise plan" } }),
    }));
    await goHash(page, "settings?tab=audit");
    // the trail and its monthly report are still there — this is the rung above
    await expect(page.getByTestId("audit-report")).toBeVisible();
    await expect(page.getByTestId("audit-reviewer")).toHaveCount(0);
  });
});
