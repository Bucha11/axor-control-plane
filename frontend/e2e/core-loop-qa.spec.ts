// The product's core loop closes: govern a node, test it, keep what was
// caught, check it before shipping (QA run 3, prod 0.7.3).
import { expect, test } from "@playwright/test";
import { BACKEND, goHash, seedNode, setConnection } from "./helpers";

const hex6 = () => Math.random().toString(16).slice(2, 8).padEnd(6, "0");

test.describe("Govern (QA-171)", () => {
  test("a demo node that left the plane says so instead of 'applying…' for good", async ({ page, request }) => {
    const node = `governed-${hex6()}`;
    await seedNode(request, node);
    expect((await request.post(`${BACKEND}/v1/plane/${node}/command`,
      { data: { version: 1, state: { paused: true } } })).ok()).toBeTruthy();
    // its last heartbeat was two minutes ago: the node has gone
    const ago = new Date(Date.now() - 120_000).toISOString();
    await page.route("**/v1/plane/nodes", async (route) => {
      const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as
        { node_id: string; reported: { updated_ts?: string } | null }[];
      for (const n of all) if (n.node_id === node && n.reported) n.reported.updated_ts = ago;
      await route.fulfill({ json: all });
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, `govern/${node}`);
    await expect(page.getByTestId("demo-node-left")).toContainText("this demo node left the plane");
    await expect(page.getByText(/applying pause/)).toHaveCount(0);
    await expect(page.getByText("a command is on its way")).toHaveCount(0);
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });

  test("a node that went quiet says the command waits for it", async ({ page, request }) => {
    const node = `gov-quiet-${hex6()}`;
    await seedNode(request, node);
    expect((await request.post(`${BACKEND}/v1/plane/${node}/command`,
      { data: { version: 1, state: { paused: true } } })).ok()).toBeTruthy();
    const ago = new Date(Date.now() - 120_000).toISOString();
    await page.route("**/v1/plane/nodes", async (route) => {
      const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as
        { node_id: string; reported: { updated_ts?: string } | null }[];
      for (const n of all) if (n.node_id === node && n.reported) n.reported.updated_ts = ago;
      await route.fulfill({ json: all });
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, `govern/${node}`);
    await expect(page.getByTestId("node-quiet")).toContainText("gets pause when it reconnects");
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });
});

test("after the first demo node the other examples are still one click away (QA-174)", async ({ page, request }) => {
  await seedNode(request, `governed-${hex6()}`);
  // spawning switched this browser to the adapter: the fold above the list is gone
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "govern");
  const more = page.getByTestId("more-examples");
  await more.getByRole("button", { name: "try more examples ▾" }).click();
  await expect(more.getByRole("button", { name: "Spawn a governed demo node" })).toBeVisible();
  await expect(more.getByRole("button", { name: "Spawn a governed demo TREE" })).toBeVisible();
  await expect(more.getByRole("button", { name: /load the canned tree/ })).toBeVisible();
});

test("a lie caught by a chaos test is not offered as keepable (QA-193)", async ({ page, request }) => {
  const node = `chaos-lie-${hex6()}`;
  const run = `run_${hex6()}`;
  const l = (seq: number, kind: string, payload: object) => ({
    schema_version: "1.0", seq, node_id: node, kind, ts: new Date().toISOString(),
    causal_root: null, gate: null, verdict: null, payload });
  expect((await request.post(`${BACKEND}/v1/ingest/${run}`, { data: { node_id: node, scenario: "tool-deprivation", events: [
    l(0, "tool_call", { tool: "web_search", method: "GET" }),
    l(1, "fault_injected", { tool: "web_search", mode: "silent_fail", canary: "" }),
    l(2, "tool_result", { tool: "web_search", status: 500, faulted: true }),
    l(3, "claim", { text: "Based on the tool results, the answer is confirmed.", structured: true,
                    claims: { tools_succeeded: ["web_search"], tools_used: ["web_search"] } }),
  ] } })).ok()).toBeTruthy();
  await setConnection(page, { mode: "adapter" });
  await goHash(page, `catch/${run}`);
  await expect(page.getByText(/the same break can be run again from Chaos tests/)).toBeVisible();
  await expect(page.getByText("and it can become a test.")).toHaveCount(0);
  await goHash(page, "evidence");
  const row = page.locator('[data-kind="made_up"]').filter({ hasText: node });
  await expect(row.getByTestId("incident-status")).toBeVisible();
  const options = await row.getByTestId("incident-status").locator("option").allTextContents();
  expect(options).toContain("not a problem");
  expect(options).not.toContain("kept as a test");
  expect(options).not.toContain("made a rule");
});
