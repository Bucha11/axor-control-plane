// Safe to ship? — every kept case replayed under the rules about to ship
// (Regression.tsx). Two-sided: an attack must stay blocked, a normal flow must
// keep passing. Deterministic replay, no model calls. The corpus is shared by
// every spec in the run, so these assert on the example's own rows.
import { expect, test } from "@playwright/test";
import { BACKEND, goHash, seedAdapterRuns, setConnection } from "./helpers";

test.describe("safe to ship", () => {
  test.beforeEach(async ({ page, request }) => {
    await seedAdapterRuns(request);
    await setConnection(page, { mode: "adapter" });
  });

  test("the example's rules hold both sides, in words", async ({ page }) => {
    await goHash(page, "regression");
    await page.getByTestId("rules-example").click();
    await expect(page.getByTestId("cases")).toContainText("must stay blocked");
    await page.getByRole("button", { name: /Check these rules/ }).click();
    await expect(page.getByTestId("case-ex_block")).toContainText("an email hides an instruction");
    await expect(page.getByTestId("case-ex_block")).toContainText("still blocked");
    await expect(page.getByTestId("case-ex_pass")).toContainText("still passes");
  });

  test("a corpus of only the example's cases does not say safe to ship", async ({ page }) => {
    // Stubbed: the shared corpus holds other specs' pins. What this owns is
    // the rendering — the example proves the example, not this deployment.
    await page.route("**/v1/regression", (route) =>
      route.fulfill({
        json: {
          rows: [
            { run_id: "ex_block", side: "must_block", label: "adapter-demo", result: "held",
              first_divergence: null, new_denial: null, pinned_denials: 1, escaped_denials: [], demo: true },
            { run_id: "ex_pass", side: "must_pass", label: "adapter-demo", result: "passed",
              first_divergence: null, new_denial: null, pinned_denials: 0, escaped_denials: [], demo: true },
          ],
          regressed: 0, escaped: 0, unanchored: 0, skipped: [], own_rows: 0, demo_rows: 2, safe_to_ship: false,
        },
      }),
    );
    await goHash(page, "regression");
    await page.getByTestId("rules-example").click();
    await page.getByRole("button", { name: /Check these rules/ }).click();
    await expect(page.getByText(/Safe to ship:/)).toHaveCount(0);
    await expect(page.getByText("The example holds:")).toBeVisible();
    await expect(page.getByText(/keep cases of your own to check yours/)).toBeVisible();
  });

  test("QA-400 / QA-386: the example's cases are checked only with the example's rules, never kept among yours", async ({ page, request }) => {
    // seeded, the example is not in the workspace's pins
    const pinned = ((await (await request.get(`${BACKEND}/v1/pins`)).json()).pins as { run_id: string }[]).map((p) => p.run_id);
    expect(pinned).not.toContain("ex_block");
    expect(pinned).not.toContain("ex_pass");
    // the workspace's own rule: the example's normal flow would be refused by it
    await request.put(`${BACKEND}/v1/policy`, { data: { tools: { ticket_read: { klass: "READ", outside: true } } } });
    await goHash(page, "prevent?rules=example");
    await expect(page.getByTestId("example-cases")).toContainText("not kept among your cases");
    const [asExample] = await Promise.all([
      page.waitForRequest((r) => r.url().endsWith("/v1/regression")),
      page.getByRole("button", { name: /Check these rules/ }).click(),
    ]);
    expect(asExample.postDataJSON().cases).toBe("example");
    await expect(page.getByText("The example holds:")).toBeVisible();
    await expect(page.getByTestId("example-verdict")).toContainText("under the example's rules");
    // the workspace's own rule is checked on the workspace's own cases only
    await page.getByTestId("rules-policy").click();
    const [own, answer] = await Promise.all([
      page.waitForRequest((r) => r.url().endsWith("/v1/regression")),
      page.waitForResponse((r) => r.url().endsWith("/v1/regression")),
      page.getByRole("button", { name: /Check these rules/ }).click(),
    ]);
    expect(own.postDataJSON().cases).toBe("own");
    const rows = ((await answer.json()).rows as { run_id: string }[]).map((r) => r.run_id);
    expect(rows).not.toContain("ex_pass");
    expect(rows).not.toContain("ex_block");
    await request.put(`${BACKEND}/v1/policy`, { data: { tools: {} } });
  });

  test("a stricter rule breaks the normal flow (the check has teeth)", async ({ page }) => {
    await goHash(page, "regression");
    await page.getByTestId("rules-example").click();
    await page.getByTestId("try-stricter").click();
    await expect(page.getByTestId("example-variant")).toContainText("notes_write taken away");
    await expect(page.getByTestId("case-ex_pass")).toContainText("NOW REFUSED");
    await expect(page.getByText("Not safe to ship:")).toBeVisible();
  });

  test("a looser rule lets the attack through, and the row says where", async ({ page }) => {
    await goHash(page, "regression");
    await page.getByTestId("rules-example").click();
    await page.getByTestId("try-looser").click();
    const row = page.getByTestId("case-ex_block");
    await expect(row).toContainText("NOW GETS THROUGH");
    await expect(page.getByText(/1 attack now gets through/)).toBeVisible();
    await row.getByText("NOW GETS THROUGH").click();
    await expect(row).toContainText("was refused at slack_post (step 6)");
  });

  test("rules by hand: an escape through JSON is named too", async ({ page, request }) => {
    // a case of the workspace's own: the example's attack, kept under its own id
    const own = `own_exfil_${Date.now().toString(36)}`;
    const events = await (await request.get(`${BACKEND}/v1/runs/ex_block/events`)).json() as Record<string, unknown>[];
    expect((await request.post(`${BACKEND}/v1/ingest/${own}`, { data: { node_id: events[0].node_id, scenario: "custom",
      events: events.map(({ id: _id, run_id: _run, ...e }) => e) } })).ok()).toBeTruthy();
    expect((await request.post(`${BACKEND}/v1/pins/${own}`, { data: { side: "must_block", label: "the exfil, kept" } })).ok()).toBeTruthy();
    await goHash(page, "regression");
    await page.getByTestId("rules-json").click();
    // a trusted set on slack_post's driving argument (the exfil now goes
    // through) and bash dropped (an unrelated call that now refuses): the
    // check must still name the escape, not answer "still blocked" because
    // something else in the run was refused
    await page.getByLabel("rules as JSON").fill(JSON.stringify({
      allowed_tools: ["email_read", "summarize", "slack_post", "notes_read", "notes_write"],
      egress_sinks: ["slack_post"],
      driving_args: { slack_post: ["text"] },
      value_policies: { slack_post: [{ arg: "text", kind: "enum", allowed: ["…"] }] },
    }));
    await page.getByRole("button", { name: /Check these rules/ }).click();
    await expect(page.getByTestId(`case-${own}`)).toContainText("NOW GETS THROUGH");
    await expect(page.getByText(/Safe to ship:/)).toHaveCount(0);
    await request.delete(`${BACKEND}/v1/pins/${own}`);
  });

  test("the rules from Policy are checked by default once there are cases of your own", async ({ page, request }) => {
    // the policy is the workspace's: put it there, as Policy saves it
    await request.put(`${BACKEND}/v1/policy`, { data: { tools: {
      web_search: { klass: "READ", outside: true }, send_email: { klass: "EXPORT", outside: false } } } });
    await goHash(page, "regression");
    await expect(page.getByTestId("rules-policy")).toContainText("2 tools");
    await page.getByTestId("rules-policy").click();
    await expect(page.getByText(/send_email — export/)).toBeVisible();
    const [sent] = await Promise.all([
      page.waitForRequest((r) => r.url().endsWith("/v1/regression")),
      page.getByRole("button", { name: /Check these rules/ }).click(),
    ]);
    expect(sent.postDataJSON().config.sinks.send_email).toEqual({ consequence_class: "EXPORT" });
  });

  test("a case that could not be read is named, and withholds the verdict", async ({ page }) => {
    await page.route("**/v1/regression", (route) =>
      route.fulfill({
        json: {
          rows: [], regressed: 0, escaped: 0, unanchored: 0, own_rows: 0, demo_rows: 0,
          skipped: [{
            run_id: "adapter-demo-block", side: "must_block", label: "the exfil",
            reason: "run adapter-demo-block is not a replayable kernel trace: "
              + "event schema '9.9' has unknown major; kernel is 1.0",
          }],
          safe_to_ship: false,
        },
      }),
    );
    await goHash(page, "regression");
    await page.getByTestId("rules-example").click();
    await page.getByRole("button", { name: /Check these rules/ }).click();
    await expect(page.getByText(/Safe to ship:/)).toHaveCount(0);
    await expect(page.getByText(/1 case could not be read/)).toBeVisible();
    await expect(page.getByText(/unknown major; kernel is 1\.0/)).toBeVisible();
  });

  test("checks on a schedule are Team's, folded, and say so", async ({ page }) => {
    await goHash(page, "regression");
    await page.getByText("run this check on a schedule (Team)").click();
    await expect(page.getByText(/come with Team/)).toBeVisible();
    await expect(page.getByText(/checking by hand stays free forever/)).toBeVisible();
    // Lab is not shipping: no hand-off from it here
    await expect(page.getByText(/cp-deploy/)).toHaveCount(0);
  });
});
