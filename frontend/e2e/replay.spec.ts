// Replay — a timeline you can question (ReplayTab.tsx). Deep-linked to the
// seeded adapter-fidelity run ex_block: the timeline boxes carry tool-name
// chips, a counterfactual fork re-gates the recorded trace deterministically
// and shows the first divergence, the provenance graph draws a value's
// derivation, and a run can be pinned into the regression corpus.
import { expect, test } from "@playwright/test";
import { goHash, SEEDED, seedAdapterRuns, setConnection } from "./helpers";

test.describe("replay", () => {
  test.beforeEach(async ({ page, request }) => {
    await seedAdapterRuns(request); // idempotent — ex_block / ex_pass
    await setConnection(page, { mode: "adapter" });
  });

  test("the timeline renders with tool-name chips, not blank boxes", async ({ page }) => {
    await goHash(page, `replay/${SEEDED.block}`);
    await expect(page.getByRole("heading", { name: `${SEEDED.block}, moment by moment.` })).toBeVisible();
    // Boxes are labelled by the tool that ran — slack_post is the egress the
    // recorded trace denies.
    await expect(page.getByText("slack_post").first()).toBeVisible();
  });

  test("a counterfactual fork re-gates the trace and finds the first divergence", async ({ page }) => {
    await goHash(page, `replay/${SEEDED.block}`);
    await page.getByRole("button", { name: /What if/ }).click();
    await page.getByRole("button", { name: "no exec capability" }).click();
    // Dropping bash from the capability table diverges at the exec step.
    await expect(page.getByText(/First divergence at/)).toBeVisible();
    await expect(page.getByText(/excluded from scores/)).toBeVisible();
  });

  test("the provenance graph draws a value's derivation", async ({ page }) => {
    await goHash(page, `replay/${SEEDED.block}`);
    // Step 1 (email_read → v_mail) produces a value ref, so selecting it focuses
    // the provenance graph on that value. Boxes are titled "N. <label>".
    await page.getByTitle(/^1\. /).click();
    await expect(page.getByText("provenance graph", { exact: false })).toBeVisible();
    await expect(page.getByText("v_mail", { exact: false }).first()).toBeVisible();
  });

  test("a run can be pinned into the regression corpus", async ({ page }) => {
    await goHash(page, `replay/${SEEDED.block}`);
    await page.getByRole("button", { name: "must-pass" }).click();
    await expect(page.getByText("run regression →")).toBeVisible();
    await expect(page.getByRole("button", { name: /✓ must-pass/ })).toBeVisible();
  });
});
