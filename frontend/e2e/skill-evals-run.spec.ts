// A skill eval started from the UI on the developer's own machine (their
// `axor-wrap worker`): the machine and the queue are stubbed on the wire, the
// page is the real one.
import { expect, test, type Page } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

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
    await new Promise((ok) => setTimeout(ok, 300));
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
  // a double-click queues one job (the QA-045/087/101 sweep)
  await page.getByTestId("eval-run").dblclick();
  await expect(page.getByTestId("eval-job-job-1")).toContainText("waiting for the machine");
  expect(queued.length).toBe(1);
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

test("times each: holds what is typed, only a whole number 1–50 runs (QA-027)", async ({ page }) => {
  await machines(page, [MINE]);
  await page.route("**/v1/machine-jobs?limit=10", (r) => r.fulfill({ json: { jobs: [] } }));
  await goHash(page, "skill-evals");
  const trials = page.getByTestId("eval-trials");
  const run = page.getByTestId("eval-run");
  const section = page.locator("label", { hasText: "times each" });
  // Backspace on 5 leaves it empty, typing 20 gives 20 — not 1, not 120
  await trials.click();
  await trials.press("End");
  await trials.press("Backspace");
  await expect(trials).toHaveValue("");
  await trials.pressSequentially("20");
  await expect(trials).toHaveValue("20");
  await expect(section).toContainText("40 agent runs");
  await expect(run).toBeEnabled();
  for (const bad of ["", "0", "-5", "3.7", "999", "1e3"]) {
    await trials.fill(bad);
    await expect(trials).toHaveValue(bad);
    await expect(section).toContainText("a whole number from 1 to 50");
    await expect(run).toBeDisabled();
  }
  await trials.fill("50");
  await expect(section).toContainText("100 agent runs");
  await expect(run).toBeEnabled();
});

test("one way to install axor-wrap: the worker's command is Skills' with the eval extra (QA-028)", async ({ page }) => {
  await machines(page, []);
  await goHash(page, "skill-evals");
  await expect(page.getByTestId("skill-eval-cmd")).toContainText("uv tool install --upgrade 'axor-wrap[plane,eval]'");
  await expect(page.getByTestId("skill-eval-cmd")).not.toContainText("pip install");
  await goHash(page, "skills");
  await expect(page.getByTestId("skill-try-cmd")).toContainText("uv tool install 'axor-wrap[plane]'");
});

test("a guest has no machines: the page does not ask for them (QA-042)", async ({ page }) => {
  const GUEST = "gs_e2e_guest_token";
  await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
    auth_enabled: true, authenticated: false, scopes: [], guest_sessions: true, guest: false } }));
  await page.route("**/v1/auth/guest", (r) => r.fulfill({ status: 201, json: {
    token: GUEST, org: "guest_e2e", ttl_seconds: 1800,
    expires_at: new Date(Date.now() + 20 * 60_000).toISOString() } }));
  const asked: string[] = [];
  let withGuest = 0;
  page.on("request", (r) => {
    if (r.headers()["authorization"] !== `Bearer ${GUEST}`) return;
    withGuest += 1;
    if (/\/v1\/(machines|machine-jobs)/.test(r.url())) asked.push(r.url());
  });
  await setConnection(page, { mode: "demo" });
  await goHash(page, "skill-evals");
  await expect(page.getByTestId("skill-eval-cmd")).toBeVisible();
  // the guest session is in place (the page asks with it); opened again with it
  await expect.poll(() => withGuest).toBeGreaterThan(0);
  await page.reload();
  await expect(page.getByTestId("skill-eval-cmd")).toBeVisible();
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(1000);
  expect(asked).toEqual([]);
});

test.describe("what a viewer is offered, and an id that is not there (QA-425, QA-426)", () => {
  const viewer = (page: Page) => page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
    auth_enabled: true, authenticated: true, scopes: ["read"] } }));

  test("QA-425: an eval id that is not there is asked once, and said in words", async ({ page }) => {
    let asked = 0;
    page.on("request", (r) => { if (r.url().includes("/v1/skill-evals/nope")) asked += 1; });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "skill-evals/nope<b>");
    await expect(page.getByText("no skill eval nope<b>")).toBeVisible();
    await page.waitForTimeout(1500);
    expect(asked).toBe(1);
  });

  test("QA-426: a viewer is offered no skill check and no worker to run", async ({ page }) => {
    await viewer(page);
    await machines(page, []);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "skills");
    await expect(page.getByTestId("skill-check-viewer")).toContainText("you can't check a skill here");
    await expect(page.getByTestId("skill-pick")).toHaveCount(0);
    await expect(page.getByTestId("skill-example")).toHaveCount(0);
    await goHash(page, "skill-evals");
    await expect(page.getByTestId("eval-viewer")).toContainText("you can't run a skill eval on a machine");
    await expect(page.getByTestId("skill-eval-cmd")).toHaveCount(0);
  });
});
