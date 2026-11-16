// Evidence — the inventory of caught discrepancies (Evidence.tsx). Before this
// screen an EvidenceCase lived only inside the run that produced it.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

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
    await page.route("**/v1/runs", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
    );
    await goHash(page, "evidence");
    await expect(page.getByTestId("evidence-empty")).toContainText("No incidents yet.");
    await page.getByRole("button", { name: /test the example agent/ }).click();
    await expect(page).toHaveURL(/#\/catch\?auto=1/);
  });
});
