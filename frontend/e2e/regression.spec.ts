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

  test("both sides of the seeded corpus hold", async ({ page }) => {
    await goHash(page, "regression");
    await page.getByRole("button", { name: /load example corpus/ }).click();
    // The seed drops its matching config into the editor.
    await expect(page.locator("textarea")).toContainText("allowed_tools");
    // North-star surface: the seeded corpus is counted in the header.
    await expect(page.getByText(/corpus: \d+ cases? pinned/)).toBeVisible();
    await expect(page.getByText(/must-block · .*must-pass/)).toBeVisible();
    await page.getByRole("button", { name: /Run regression/ }).click();

    await expect(page.getByText("still blocked").first()).toBeVisible();
    await expect(page.getByText("still passes").first()).toBeVisible();
  });

  test("a corpus of only canned pins cannot say it is safe", async ({ page }) => {
    // Stubbed, not seeded: the e2e stack shares one backend and other specs pin
    // runs of their own into the same corpus, so "none of your own" is not a
    // state this test can hold. What it owns is the rendering.
    //
    // This headline exists because the demo seed was measured doing what an
    // empty corpus used to: turning "nothing about this deployment was
    // verified" into a green verdict. The rule itself is pinned backend-side.
    await page.route("**/v1/regression", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          rows: [
            { run_id: "ex_block", side: "must_block", label: "adapter-demo",
              result: "held", first_divergence: null, new_denial: null,
              pinned_denials: 1, escaped_denials: [], demo: true },
            { run_id: "ex_pass", side: "must_pass", label: "adapter-demo",
              result: "passed", first_divergence: null, new_denial: null,
              pinned_denials: 0, escaped_denials: [], demo: true },
          ],
          regressed: 0, escaped: 0, unanchored: 0, skipped: [],
          own_rows: 0, demo_rows: 2, safe_to_ship: false,
        }),
      }),
    );
    await goHash(page, "regression");
    await page.getByRole("button", { name: /load example corpus/ }).click();
    await page.getByRole("button", { name: /Run regression/ }).click();

    await expect(page.getByText(/Safe to ship/)).toHaveCount(0);
    await expect(page.getByText(/2 demo pins, none of your own/)).toBeVisible();
    await expect(page.getByText(/Pin one of your own runs/)).toBeVisible();
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

  test("a config that lets the pinned exfil through escapes, and the row names it", async ({ page }) => {
    await goHash(page, "regression");
    // Two edits at once, both plausible: a trusted set on slack_post's driving
    // argument (which supersedes the integrity axis, so the exfil the trace
    // recorded as DENY now goes through), and bash dropped from the capability
    // table (an unrelated benign call that now denies). The report used to
    // answer "still blocked" for exactly this — it asked whether the config
    // denies ANYTHING in the trace, and bash obliged.
    await page.locator("textarea").fill(
      JSON.stringify(
        {
          allowed_tools: ["email_read", "summarize", "slack_post", "notes_read", "notes_write"],
          egress_sinks: ["slack_post"],
          driving_args: { slack_post: ["text"] },
          value_policies: {
            slack_post: [{ arg: "text", kind: "enum", allowed: ["…"] }],
          },
        },
        null,
        2,
      ),
    );
    await page.getByRole("button", { name: /Run regression/ }).click();

    await expect(page.getByText("ESCAPED", { exact: true })).toBeVisible();
    await expect(page.getByText(/Safe to ship/)).toHaveCount(0);
    // Counts only for the escape itself: the corpus is shared with whatever
    // other specs in this run have pinned, so "how many held" is not this
    // test's to assert.
    await expect(page.getByText(/1 escape/).first()).toBeVisible();
    // The row opens onto the step that escaped — named, not a count.
    await page.getByText("ESCAPED", { exact: true }).click();
    await expect(
      page.getByText(/slack_post @ adapter-demo-block:6.*recorded DENY/),
    ).toBeVisible();
  });

  // A pin whose trace cannot be read is stubbed rather than seeded: the backend
  // now refuses such a line at ingest (limits.check_batch), which is the point —
  // the state only arises from rows stored before that door, or a kernel upgrade
  // that drops a major it once read. The suite also shares one backend and one
  // tenant, so a poisoned pin would leak into every other regression test.
  test("a pin the corpus could not read is named, and withholds the verdict", async ({ page }) => {
    await page.route("**/v1/regression", (route) =>
      route.fulfill({
        json: {
          rows: [],
          regressed: 0,
          escaped: 0,
          unanchored: 0,
          skipped: [{
            run_id: "adapter-demo-block",
            side: "must_block",
            label: "the exfil",
            reason: "run adapter-demo-block is not a replayable kernel trace: "
              + "event schema '9.9' has unknown major; kernel is 1.0",
          }],
          safe_to_ship: false,
        },
      }),
    );
    await goHash(page, "regression");
    await page.getByRole("button", { name: /Run regression/ }).click();

    // Not "Safe to ship", and not "No pinned traces" either — something IS
    // pinned, and telling the operator to go run an experiment would send them
    // after a problem they do not have.
    await expect(page.getByText(/Safe to ship/)).toHaveCount(0);
    await expect(page.getByText(/No pinned traces/)).toHaveCount(0);
    await expect(page.getByText(/1 unreadable pin/).first()).toBeVisible();
    // The pin, and the reason — "1 skipped" alone leaves no next move.
    await expect(page.getByText(/Pinned, but the trace could not be read/)).toBeVisible();
    await expect(page.getByText(/unknown major; kernel is 1\.0/)).toBeVisible();
  });

  test("the org layer (schedule + history) renders locked without a license", async ({ page }) => {
    await goHash(page, "regression");
    await expect(page.getByText("SCHEDULED CI · HISTORY")).toBeVisible();
    // Honest upsell: the lock names the tier and what stays free.
    await expect(page.getByText(/scheduled corpus runs \+ history are org features/)).toBeVisible();
    await expect(page.getByText(/manual runs stay free forever/)).toBeVisible();
  });
});
