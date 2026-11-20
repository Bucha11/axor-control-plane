// Agents — the list shows the menu's side: coding agents (sessions and their
// tree runs) or production agents; what an agent did that needs a look is
// named, and opens its incidents.
import { expect, test } from "@playwright/test";
import { BACKEND, goHash } from "./helpers";

async function as(page: import("@playwright/test").Page, journey: string): Promise<void> {
  await page.addInitScript((j) => window.localStorage.setItem("axor-app", JSON.stringify({
    state: { connection: { mode: "adapter", tools: [], testBench: false }, journey: j }, version: 0 })), journey);
}

test.beforeEach(async ({ request }) => {
  for (const seed of ["seed-coding", "seed-fleet"]) {
    expect((await request.post(`${BACKEND}/v1/demo/${seed}`)).ok()).toBeTruthy();
  }
});

test("coding agents only, and the rest one click away", async ({ page }) => {
  await as(page, "coding");
  await goHash(page, "govern");
  await expect(page.getByTestId("agent-row-claude-code-example-3f9a21c0")).toBeVisible();
  await expect(page.getByTestId("agent-row-tree-orch")).toHaveCount(0);
  await page.getByTestId("agents-show-other").click();
  await expect(page.getByTestId("agent-row-tree-orch")).toBeVisible();
});

test("production agents only, without the coding sessions", async ({ page }) => {
  await as(page, "production");
  await goHash(page, "govern");
  await expect(page.getByTestId("agent-row-tree-orch")).toBeVisible();
  await expect(page.getByTestId("agent-row-claude-code-example-3f9a21c0")).toHaveCount(0);
});

test("what an agent did that needs a look opens its incidents", async ({ page }) => {
  await as(page, "coding");
  await goHash(page, "govern");
  const look = page.getByTestId("agent-look-claude-code-example-3f9a21c0");
  await expect(look).toContainText("said the checks passed when they failed");
  await look.click();
  await expect(page).toHaveURL(/#\/evidence\?agent=claude-code-example-3f9a21c0/);
  await expect(page.getByLabel("agent")).toHaveValue("claude-code-example-3f9a21c0");
});
