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
