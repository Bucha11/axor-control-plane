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
    // the example's own: other specs record its events as an agent's own run
    const row = page.getByTestId("search-row").filter({ hasText: "DROP TABLE orders" })
      .filter({ hasText: "support-agent-prod" });
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
    await expect(row).toHaveCount(1);
  });

  // QA-031: scrubbing kept `?seq=4` in the URL, so a reload or a shared link
  // went back to step 5; a seq the run does not have was ignored silently.
  test("the URL follows the step, and a step the run does not have is said", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-prod-incident`)).ok()).toBeTruthy();
    await goHash(page, "explain/ex_prod_drop?seq=4&node=support-agent-prod");
    await expect(page.getByTestId("replay-step")).toContainText("STEP 5 OF 7");
    await page.getByTestId("step-7").click();
    await expect(page.getByTestId("replay-step")).toContainText("STEP 7 OF 7");
    await expect(page).toHaveURL(/cursor=6/);
    await expect(page).not.toHaveURL(/seq=/);
    await page.reload();
    await expect(page.getByTestId("replay-step")).toContainText("STEP 7 OF 7");

    await goHash(page, "explain/ex_prod_drop?seq=999&node=support-agent-prod");
    await page.reload();
    await expect(page.getByTestId("replay-seq-missing")).toContainText("999");
    await expect(page.getByTestId("replay-step")).toContainText("STEP 1 OF 7");
  });

  test("make it a rule: the drop is refused, the agent's normal work still passes", async ({ page, request }) => {
    // the agent's own runs (the example's events, recorded as this workspace's
    // agent): on the example itself nothing is written (QA-194, below)
    expect((await request.post(`${BACKEND}/v1/demo/seed-prod-incident`)).ok()).toBeTruthy();
    const node = `support-agent-own-${Date.now().toString(36)}`;
    const own = { drop: `${node}-drop`, ok: `${node}-ok` };
    for (const [from, to] of [["ex_prod_ok", own.ok], ["ex_prod_drop", own.drop]] as const) {
      const events = (await (await request.get(`${BACKEND}/v1/runs/${from}/events`)).json()) as Record<string, unknown>[];
      const r = await request.post(`${BACKEND}/v1/ingest/${to}`, { data: { node_id: node, scenario: "custom",
        events: events.map(({ id: _id, run_id: _run, ...e }) => ({ ...e, node_id: node })) } });
      expect(r.ok(), await r.text()).toBeTruthy();
    }
    await goHash(page, `explain/${own.drop}?seq=4&node=${node}`);
    await expect(page.getByTestId("replay-args")).toContainText("DROP TABLE");
    await page.getByTestId("whatif-outside").click();
    await expect(page.getByTestId("whatif-answer")).toContainText("step 5 (bash): refused");
    await expect(page.getByTestId("whatif-answer")).toContainText("steps 1–4 go as before");
    const rule = page.getByTestId("make-rule");
    await expect(rule).toContainText("ticket_reply sends out");   // from the agent's other run
    await expect(rule.getByTestId("make-rule-example")).toHaveCount(0);
    await rule.getByTestId("make-rule-apply").click();
    await expect(page).toHaveURL(/#\/prevent\?rules=policy/);
    await page.getByRole("button", { name: /Check these rules/ }).click();
    const drop = page.getByText(`${node}: bash built from outside text`).locator("..");
    await expect(drop).toContainText("still blocked");
    await expect(page.getByText(`normal work of ${node}`).locator("..")).toContainText("still passes");
    for (const r of [own.drop, own.ok]) await request.delete(`${BACKEND}/v1/pins/${r}`);
    await request.put(`${BACKEND}/v1/policy`, { data: { tools: {} } });
  });

  test("QA-194: on the example, make it a rule and keep it write nothing of the workspace's", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-prod-incident`)).ok()).toBeTruthy();
    const policyBefore = (await (await request.get(`${BACKEND}/v1/policy`)).json()).tools;
    const pinned = async () => ((await (await request.get(`${BACKEND}/v1/pins`)).json()).pins as { run_id: string }[])
      .map((p) => p.run_id).filter((id) => id === "ex_prod_drop" || id === "ex_prod_ok");
    const pinsBefore = await pinned();
    await goHash(page, "explain/ex_prod_drop?seq=4&node=support-agent-prod");
    await expect(page.getByTestId("replay-args")).toContainText("DROP TABLE");
    await page.getByTestId("whatif-outside").click();
    const rule = page.getByTestId("make-rule");
    await expect(rule.getByTestId("make-rule-example")).toContainText("This is the example: nothing here goes into your Policy");
    await expect(rule.getByTestId("make-rule-apply")).toHaveCount(0);
    await expect(page.getByTestId("replay-keep")).toHaveCount(0);
    await expect(page.getByTestId("replay-keep-example")).toBeVisible();
    await rule.getByTestId("make-rule-example-check").click();
    await expect(page).toHaveURL(/#\/prevent\?rules=example/);
    expect((await (await request.get(`${BACKEND}/v1/policy`)).json()).tools).toEqual(policyBefore);
    expect(await pinned()).toEqual(pinsBefore);
    const drop = ((await (await request.get(`${BACKEND}/v1/incidents`)).json()).items as { id: string; status: string }[])
      .find((i) => i.id.startsWith("step:ex_prod_drop:"));
    expect(drop?.status).not.toBe("rule_made");
  });
});
