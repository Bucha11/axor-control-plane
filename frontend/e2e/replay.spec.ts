// Replay — a run, step by step (ReplayTab.tsx). Deep-linked to the seeded
// adapter-fidelity run ex_block: each step is a box named by its tool, the
// selected step reads as one sentence (refused — and why), "what if" decides
// the same recorded steps again under other rules and names the first step
// that would have gone differently, and a run can be kept as a test.
import { expect, test } from "@playwright/test";
import { BACKEND, goHash, SEEDED, seedAdapterRuns, setConnection } from "./helpers";

test.describe("replay", () => {
  test.beforeEach(async ({ page, request }) => {
    await seedAdapterRuns(request); // idempotent — ex_block / ex_pass
    await setConnection(page, { mode: "adapter" });
  });

  test("the run reads in words: what happened, then each step", async ({ page }) => {
    await goHash(page, `replay/${SEEDED.block}`);
    await expect(page.getByTestId("replay-title")).toBeVisible();
    await expect(page.getByTestId("replay-summary")).toContainText(/\d+ steps/);
    await expect(page.getByTestId("replay-summary")).toContainText(/refused at step \d+/);
    // Boxes are labelled by the tool that ran — slack_post is the send-out the
    // recorded trace refuses.
    await expect(page.getByTestId("replay-steps").getByText(/slack_post/).first()).toBeVisible();
    await page.getByTestId("replay-steps").getByTitle(/slack_post — refused/).first().click();
    await expect(page.getByTestId("replay-step")).toContainText(/slack_post — refused/);
  });

  test("what if: the same steps decided again find the first step that changes", async ({ page }) => {
    await goHash(page, `replay/${SEEDED.block}`);
    await page.getByTestId("whatif-noexec").click();
    await expect(page.getByTestId("whatif-answer")).toContainText(/It would have gone differently at step \d+/);
    await expect(page.getByTestId("whatif-answer")).toContainText("shown faded");
  });

  test("an answer's details fold out the value graph", async ({ page }) => {
    await goHash(page, `replay/${SEEDED.block}`);
    // email_read's answer is the value v_mail.
    await page.getByTitle(/^\d+\. email_read answered/).first().click();
    await page.getByRole("button", { name: /details/ }).click();
    await expect(page.getByText("v_mail", { exact: false }).first()).toBeVisible();
  });

  test("a run can be kept as a test", async ({ page }) => {
    await goHash(page, `replay/${SEEDED.block}`);
    await page.getByRole("button", { name: /normal work — must keep passing/ }).click();
    await expect(page.getByText("check it in Safe to ship? →")).toBeVisible();
    await expect(page.getByRole("button", { name: /✓ normal work/ })).toBeVisible();
  });

  test("find the step: Thursday, prod, a table dropped — then where it came from", async ({ page, request }) => {
    const r = await request.post(`${BACKEND}/v1/demo/seed-prod-incident`);
    expect(r.ok()).toBeTruthy();
    await goHash(page, "explain?when=30d");
    await page.getByTestId("search-filters").getByRole("button", { name: "deleted or wiped" }).click();
    await page.getByLabel("contains").fill("orders");
    const row = page.getByTestId("search-row").filter({ hasText: "DROP TABLE orders" });
    await expect(row).toHaveCount(1);
    await expect(row).toContainText("let through");
    await expect(row).toContainText("used text from outside");
    await row.click();
    // Replay opens on that very call, with the chain it came from
    await expect(page.getByTestId("replay-title")).toContainText("support-agent-prod");
    await expect(page.getByTestId("replay-step")).toContainText("STEP 5 OF 7");
    await expect(page.getByTestId("replay-args")).toContainText("DROP TABLE orders CASCADE");
    await expect(page.getByTestId("replay-origin")).toContainText("ticket_read's answer (outside text)");
    // and back to the same results
    await page.getByTestId("replay-back").click();
    await expect(page.getByLabel("contains")).toHaveValue("orders");
    await expect(page.getByTestId("search-row").filter({ hasText: "DROP TABLE orders" })).toHaveCount(1);
  });

  test("make it a rule: the drop is refused, the agent's normal work still passes", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-prod-incident`)).ok()).toBeTruthy();
    await goHash(page, "explain/ex_prod_drop?seq=4&node=support-agent-prod");
    await expect(page.getByTestId("replay-args")).toContainText("DROP TABLE");
    await page.getByTestId("whatif-outside").click();
    await expect(page.getByTestId("whatif-answer")).toContainText("step 5 (bash): refused");
    await expect(page.getByTestId("whatif-answer")).toContainText("steps 1–4 go as before");
    const rule = page.getByTestId("make-rule");
    await expect(rule).toContainText("ticket_reply sends out");   // from the agent's other run
    await rule.getByTestId("make-rule-apply").click();
    await expect(page).toHaveURL(/#\/prevent\?rules=policy/);
    await page.getByRole("button", { name: /Check these rules/ }).click();
    const drop = page.getByText("support-agent-prod: bash built from outside text").locator("..");
    await expect(drop).toContainText("still blocked");
    await expect(page.getByText("normal work of support-agent-prod").locator("..")).toContainText("still passes");
  });
});
