// A skill eval started from the UI on the developer's own machine (their
// `axor-wrap worker`): the machine and the queue are stubbed on the wire, the
// page is the real one.
import { expect, test, type Page } from "@playwright/test";
import { goHash } from "./helpers";

const MINE = { machine_id: "m-laptop", host: "laptop", repo_label: "acme/api", agents: ["claude-code"],
               last_seen: new Date().toISOString(), online: true, yours: true };

async function machines(page: Page, list: unknown[]) {
  await page.route("**/v1/machines", (r) => r.fulfill({ json: { machines: list } }));
}

test("Run queues the eval on your machine, and the page follows it to the report", async ({ page }) => {
  await machines(page, [
    MINE,
    { ...MINE, machine_id: "m-bob", host: "bob-laptop", yours: false },        // someone else's
    { ...MINE, machine_id: "m-old", host: "old-laptop", online: false },        // gone quiet
  ]);
  const queued: unknown[] = [];
  let jobs: unknown[] = [];
  const job = (over: Record<string, unknown>) => ({
    job_id: "job-1", machine_id: "m-laptop", created_by: "ada@acme.io", kind: "skill_eval",
    spec: { skills: ["./.claude/skills/changelog"], agent: "claude-code", trials: 3, tasks: [] },
    state: "queued", cancel_requested: false, lease_until: null, progress: {}, eval_id: null,
    error: "", created_ts: "2026-11-26T12:00:00Z", updated_ts: "2026-11-26T12:00:00Z", ...over });
  await page.route("**/v1/machine-jobs?limit=10", (r) => r.fulfill({ json: { jobs } }));
  await page.route("**/v1/machine-jobs", async (r) => {
    queued.push(r.request().postDataJSON());
    jobs = [job({})];
    await r.fulfill({ status: 201, json: jobs[0] });
  });

  await goHash(page, "skill-evals");
  // only your own machine that is on line is offered
  await expect(page.getByTestId("eval-machine").locator("option")).toHaveText(
    ["acme/api · laptop · claude-code"]);
  await expect(page.getByTestId("skill-eval-cmd")).toHaveCount(0);   // nothing to copy

  await page.getByTestId("eval-skill").fill("./.claude/skills/changelog");
  await page.getByTestId("eval-trials").fill("3");
  await page.getByTestId("eval-run").click();
  await expect.poll(() => queued.length).toBe(1);
  expect(queued[0]).toEqual({ machine_id: "m-laptop", kind: "skill_eval", spec: {
    skills: ["./.claude/skills/changelog"], agent: "claude-code", trials: 3,
    tasks: [{ name: "task-1", prompt: "fix the failing test in tests/", check: "python -m pytest -q" }] } });
  await expect(page.getByTestId("eval-job-job-1")).toContainText("waiting for the machine");

  jobs = [job({ state: "running", progress: { done: 2, total: 6, solved: 1 } })];
  await expect(page.getByTestId("eval-job-job-1")).toContainText("2 / 6 runs · 1 solved");
  await expect(page.getByTestId("eval-job-cancel-job-1")).toBeVisible();

  jobs = [job({ state: "done", eval_id: "se-run1", progress: { done: 6, total: 6, solved: 4 } })];
  await page.getByTestId("eval-job-report-job-1").click();
  await expect(page).toHaveURL(/#\/skill-evals\/se-run1$/);
});

test("with no machine of yours on line, the one command that brings one", async ({ page }) => {
  await machines(page, [{ ...MINE, yours: false }]);
  await goHash(page, "skill-evals");
  await expect(page.getByTestId("eval-no-machine")).toContainText("no machine of yours is on line");
  await expect(page.getByTestId("skill-eval-cmd")).toContainText("axor-wrap worker");
  await expect(page.getByTestId("eval-run")).toHaveCount(0);
});
