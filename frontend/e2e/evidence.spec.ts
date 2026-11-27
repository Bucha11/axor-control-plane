// Evidence — the inventory of caught discrepancies (Evidence.tsx). Before this
// screen an EvidenceCase lived only inside the run that produced it.
import { expect, test } from "@playwright/test";
import { BACKEND, goHash, setConnection } from "./helpers";

test.describe("evidence", () => {
  test("a case caught by a fault test is listed, and opens in Replay", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "catch");
    await page.getByRole("button", { name: /Run the test/ }).click();
    await expect(page.getByText("fabricated a tool result").first()).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: "Incidents", exact: true }).click();
    const row = page.getByTestId("evidence-row").first();
    await expect(row).toBeVisible();
    await expect(row).toContainText("said a failed tool worked");
    await expect(row).toContainText("THE AGENT SAID");

    await row.getByRole("button", { name: /replay it/ }).click();
    await expect(page).toHaveURL(/#\/explain\/[^/?]+/);
  });

  test("a proxy-connected visit with no route lands on Incidents", async ({ page }) => {
    await setConnection(page, { mode: "proxy" });
    await page.goto("/");
    await expect(page).toHaveURL(/#\/evidence$/);
    await expect(page.getByRole("heading", { name: "Incidents" })).toBeVisible();
  });

  test("an empty inventory says how to catch the first one", async ({ page }) => {
    await page.route("**/v1/incidents**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ items: [], new: { made_up: 0, dangerous: 0, would_refuse: 0, refused: 0 },
                               needs_a_look: 0 }) }),
    );
    await goHash(page, "evidence");
    await expect(page.getByTestId("evidence-empty")).toContainText("No incidents yet.");
    await page.getByRole("button", { name: /test the example agent/ }).click();
    await expect(page).toHaveURL(/#\/catch\?auto=1/);
  });

  test("the menu's count is what the page lists first (QA-040)", async ({ page, request }) => {
    // the tree example has a made-up answer and a refused send-out, both new:
    // the badge counted 2, the default view (refusals left out) listed 1
    expect((await request.post(`${BACKEND}/v1/demo/seed-tree-run`)).ok()).toBeTruthy();
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "evidence");
    const badge = page.getByTestId("incidents-count");
    await expect(badge).toBeVisible();
    const listed = page.getByTestId("evidence-list").locator(":scope > [data-kind]");
    await expect(listed.first()).toBeVisible();
    await expect(page.getByTestId("evidence-list").locator(':scope > [data-kind="refused"]').first()).toBeVisible();
    expect(await listed.count()).toBe(Number(await badge.textContent()));
  });

  test("a dangerous call let through is in the feed; settling it takes it off the new list", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-prod-incident`)).ok()).toBeTruthy();
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "evidence");
    await page.getByTestId("incident-filters").getByRole("button", { name: /dangerous, let through/ }).click();
    // the example's own: other specs record its events as an agent's own run
    const drop = page.locator('[data-kind="dangerous"]').filter({ hasText: "DROP TABLE orders" })
      .filter({ hasText: "support-agent-prod · example data" });
    await expect(drop).toContainText("deleted or wiped something");
    await expect(drop).toContainText("agent support-agent-prod · example data");
    await drop.getByTestId("incident-status").selectOption("not_a_problem");
    await expect(drop).toHaveCount(0);
    await page.getByRole("button", { name: "any status" }).click();
    const again = drop;
    await expect(again.getByTestId("incident-status")).toHaveValue("not_a_problem");
    await again.getByRole("button", { name: /where it came from/ }).click();
    await expect(page).toHaveURL(/#\/explain\/ex_prod_drop\?seq=4/);
    // and back, for the next one
    await again.page().goBack();
    await again.getByTestId("incident-status").selectOption("new");
  });
});
