// Agents — the list shows the menu's side: coding agents (sessions and their
// tree runs) or production agents; what an agent did that needs a look is
// named, and opens its incidents. The plane is answered here (other specs
// share the real one and expect it as they leave it).
import { expect, test, type Page } from "@playwright/test";
import { goHash } from "./helpers";

const CODER = "claude-code-e2e-agents";
const PROD = "prod-e2e-agents";
const node = (id: string, profile: object | null) => ({
  node_id: id, desired: null, facts: [], reputation: null, probe: null, profile,
  reported: { applied_version: 0, level: "NORMAL", budget_remaining: null, updated_ts: new Date().toISOString() },
});

async function as(page: Page, journey: string): Promise<void> {
  await page.addInitScript((j) => window.localStorage.setItem("axor-app", JSON.stringify({
    state: { connection: { mode: "adapter", tools: [], testBench: false }, journey: j }, version: 0 })), journey);
  await page.route("**/v1/plane/nodes", (r) => r.fulfill({ json: [
    node(CODER, { kind: "coding", agent: "claude-code", developer: "dev-e2e" }), node(PROD, null)] }));
  await page.route("**/v1/incidents**", (r) => r.fulfill({ json: {
    items: [{ id: "ev:r1:0", kind: "made_up", ts: new Date().toISOString(), node_id: CODER, run_id: "r1",
              seq: null, what: "claimed_checks_passed", detail: "", count: 1, status: "new" }],
    new: { made_up: 1, dangerous: 0, would_refuse: 0, refused: 0 }, needs_a_look: 1 } }));
}

test("coding agents only, and the rest one click away", async ({ page }) => {
  await as(page, "coding");
  await goHash(page, "govern");
  await expect(page.getByTestId(`agent-row-${CODER}`)).toBeVisible();
  await expect(page.getByTestId(`agent-row-${PROD}`)).toHaveCount(0);
  await page.getByTestId("agents-show-other").click();
  await expect(page.getByTestId(`agent-row-${PROD}`)).toBeVisible();
});

test("production agents only, without the coding sessions", async ({ page }) => {
  await as(page, "production");
  await goHash(page, "govern");
  await expect(page.getByTestId(`agent-row-${PROD}`)).toBeVisible();
  await expect(page.getByTestId(`agent-row-${CODER}`)).toHaveCount(0);
});

test("what an agent did that needs a look: on its row, in its panel, in Incidents", async ({ page }) => {
  await as(page, "coding");
  await goHash(page, "govern");
  await page.getByTestId(`agent-row-${CODER}`).click();
  const panel = page.getByTestId("agent-panel");
  await expect(panel.getByTestId("agent-who")).toContainText("Claude Code session · developer dev-e2e");
  await expect(panel.getByTestId("agent-needs-a-look")).toContainText("said the checks passed when they failed");
  await expect(panel.getByTestId("agent-chaos-link")).toHaveCount(0);   // no chaos on a coding session
  const look = page.getByTestId(`agent-look-${CODER}`);
  await expect(look).toContainText("said the checks passed when they failed");
  await look.click();
  await expect(page).toHaveURL(new RegExp(`#/evidence\\?agent=${CODER}`));
});
