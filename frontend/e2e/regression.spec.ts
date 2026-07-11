// Regression — two-sided, deterministic CI (Regression.tsx). The pinned corpus
// (a must-block attack + a must-pass legit flow) is replayed under a candidate
// config: a good config is safe to ship; a config that breaks the legit flow
// regresses. Deterministic replay, no model calls.
import { expect, test } from "@playwright/test";
import { goHash, seedAdapterRuns, setConnection } from "./helpers";

test.describe("regression", () => {
  test.beforeEach(async ({ page, request }) => {
    await seedAdapterRuns(request);
    await setConnection(page, { mode: "adapter" });
  });

  test("the golden config is safe to ship (both sides hold)", async ({ page }) => {
    await goHash(page, "regression");
    await page.getByRole("button", { name: /load example corpus/ }).click();
    // The seed drops its matching config into the editor.
    await expect(page.locator("textarea")).toContainText("allowed_tools");
    await page.getByRole("button", { name: /Run regression/ }).click();

    await expect(page.getByText(/Safe to ship/)).toBeVisible();
    await expect(page.getByText("still blocked").first()).toBeVisible();
    await expect(page.getByText("still passes").first()).toBeVisible();
  });

  test("a config that breaks the legit flow regresses (the CI has teeth)", async ({ page }) => {
    await goHash(page, "regression");
    // Drop notes_write — the must-pass flow needs it, so it now blocks: a
    // regression the one-sided view would miss.
    await page.locator("textarea").fill(
      JSON.stringify(
        {
          allowed_tools: ["email_read", "bash", "summarize", "slack_post", "notes_read"],
          egress_sinks: ["slack_post"],
        },
        null,
        2,
      ),
    );
    await page.getByRole("button", { name: /Run regression/ }).click();
    await expect(page.getByText(/changes governed behavior/)).toBeVisible();
    await expect(page.getByText("REGRESSED").first()).toBeVisible();
    await expect(page.getByText(/Safe to ship/)).toHaveCount(0);
  });

  test("the org layer (schedule + history) renders locked without a license", async ({ page }) => {
    await goHash(page, "regression");
    await expect(page.getByText("SCHEDULED CI · HISTORY")).toBeVisible();
    // Honest upsell: the lock names the tier and what stays free.
    await expect(page.getByText(/scheduled corpus runs \+ history are org features/)).toBeVisible();
    await expect(page.getByText(/manual runs stay free forever/)).toBeVisible();
  });
});
