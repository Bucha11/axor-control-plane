// A developer's coding sessions fold into one row on Agents: a day of Claude
// Code is many sessions, and the list is about people's agents.
import { expect, test } from "@playwright/test";
import { BACKEND, goHash, seedNode, setConnection } from "./helpers";

test("two sessions of one developer are one row, unfolded on a click", async ({ page, request }) => {
  const tag = Date.now().toString(36).slice(-6);
  const dev = `dev-zed-${tag}`;
  const nodes = [`claude-code-zed-${tag}-a`, `claude-code-zed-${tag}-b`];
  for (const n of nodes) {
    await seedNode(request, n);
    const r = await request.post(`${BACKEND}/v1/plane/${n}/profile`, {
      data: { kind: "coding", agent: "claude-code", developer: dev } });
    expect(r.ok()).toBeTruthy();
  }
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "govern");
  const row = page.getByTestId(`developer-row-${dev}`);
  await expect(row).toContainText("2 sessions");
  await expect(row).toContainText("Claude Code");
  await expect(page.getByTestId(`agent-row-${nodes[0]}`)).toHaveCount(0);
  await row.getByText("▸ show sessions").click();
  for (const n of nodes) await expect(page.getByTestId(`agent-row-${n}`)).toBeVisible();
});

test("a session reads as what it was asked first", async ({ page, request }) => {
  const node = `claude-code-title-${Date.now().toString(36).slice(-6)}`;
  await seedNode(request, node);
  await request.post(`${BACKEND}/v1/plane/${node}/profile`, {
    data: { kind: "coding", agent: "claude-code", developer: `dev-title-${node}` } });
  const r = await request.post(`${BACKEND}/v1/plane/${node}/title`, { data: { title: "add a 10% discount over $100" } });
  expect(r.ok()).toBeTruthy();
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "govern");
  await page.getByTestId(`developer-row-dev-title-${node}`).getByText("▸ show sessions").click();
  await expect(page.getByTestId(`agent-row-${node}`)).toContainText("add a 10% discount over $100");
});

test("a subagent is a branch under its session, and its panel leads back to it", async ({ page, request }) => {
  const tag = Date.now().toString(36).slice(-6);
  const dev = `dev-sub-${tag}`;
  const parent = `claude-code-sub-${tag}`;
  const sub = `${parent}.Explore-ab12cd34`;
  for (const [n, extra] of [[parent, {}], [sub, { parent }]] as const) {
    await seedNode(request, n);
    const r = await request.post(`${BACKEND}/v1/plane/${n}/profile`, {
      data: { kind: "coding", agent: "claude-code", developer: dev, ...extra } });
    expect(r.ok()).toBeTruthy();
  }
  await request.post(`${BACKEND}/v1/plane/${parent}/title`, { data: { title: "add login rate limiting" } });
  await request.post(`${BACKEND}/v1/plane/${sub}/title`, { data: { title: "Explore: find every login handler" } });
  // on the Coding plan: this developer's sessions are shown and operated
  await page.route("**/v1/license/entitlements", (route) => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({
      tier: "coding", free: false, developers: null, agent_families: null, agent_family: null,
      locked: {}, locked_trees: {}, reasons: {} }) }));
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "govern");
  // one session, not two: the subagent is counted with the session
  const row = page.getByTestId(`developer-row-${dev}`);
  await expect(row).toContainText("1 session");
  await row.getByText("▸ show sessions").click();
  await expect(page.getByTestId(`agent-row-${sub}`)).toHaveCount(0);
  const branch = page.getByTestId(`subagent-row-${sub}`);
  await expect(branch).toContainText("Explore: find every login handler");
  await expect(branch).toContainText("subagent");
  await branch.click();
  await expect(page.getByTestId("agent-panel-parent")).toContainText("a subagent of “add login rate limiting”");
  await page.getByTestId("agent-panel-parent").getByText(/add login rate limiting/).click();
  await expect(page.getByTestId("agent-panel")).toContainText(parent);
});
