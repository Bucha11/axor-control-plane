// The sign-up funnel: first-party, anonymous, and silent under Do Not Track.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe("sign-up funnel", () => {
  test("a visit is counted and the operator reads the funnel in Settings", async ({ page }) => {
    const posted: { event: string; session_id: string }[] = [];
    page.on("request", (r) => {
      if (r.url().endsWith("/v1/funnel") && r.method() === "POST") posted.push(r.postDataJSON());
    });
    await setConnection(page, { mode: "demo" });
    await goHash(page, "settings");
    await expect.poll(() => posted.some((p) => p.event === "visit")).toBe(true);
    // a random tab id — nothing personal
    expect(posted[0].session_id).toMatch(/^t[0-9a-f]{24}$/);

    // with auth off the e2e stack is its own operator, so the panel shows
    const table = page.getByTestId("funnel-table");
    await expect(table).toBeVisible();
    await expect(table.getByText("visited")).toBeVisible();
    await expect(table.getByText("signed up")).toBeVisible();
  });

  test("Do Not Track sends nothing", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(Navigator.prototype, "doNotTrack", { get: () => "1" });
    });
    let sent = 0;
    page.on("request", (r) => {
      if (r.url().endsWith("/v1/funnel") && r.method() === "POST") sent += 1;
    });
    await setConnection(page, { mode: "demo" });
    await goHash(page, "govern");
    await page.waitForTimeout(1500);
    expect(sent).toBe(0);
  });
});
