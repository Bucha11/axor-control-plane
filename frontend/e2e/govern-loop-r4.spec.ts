// Govern and the core loop (QA run 4, prod 0.7.4).
import { expect, test, type Page, type Route } from "@playwright/test";
import { BACKEND, goHash, seedNode, setConnection } from "./helpers";

const hex6 = () => Math.random().toString(16).slice(2, 8).padEnd(6, "0");

/** The plane refuses the first command for want of a key; /v1/operators
 *  registers one. Returns what was sent, and how many keys were made. */
async function keylessPlane(page: Page, node: string) {
  const sent: { operator: string; sig: string }[] = [];
  const made = { n: 0 };
  await page.route(`**/v1/plane/${node}/command`, async (r: Route) => {
    sent.push(r.request().postDataJSON());
    if (sent.length === 1) return r.fulfill({ status: 403, json: { detail: "no operator keys registered; commands rejected" } });
    return r.continue();
  });
  await page.route("**/v1/operators", async (r: Route) => {
    if (r.request().method() !== "POST") return r.continue();
    made.n += 1;
    const b = r.request().postDataJSON() as { pubkey: string };
    await r.fulfill({ status: 201, json: { operator_id: `owner#${b.pubkey.slice(0, 8)}` } });
  });
  return { sent, made };
}

test.describe("a command refused for want of an operator key (QA-501)", () => {
  test("the Agents panel makes the key in place, then sends the pause", async ({ page, request }) => {
    const node = `gov-key-${hex6()}`;
    await seedNode(request, node);
    const { sent, made } = await keylessPlane(page, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, `govern/${node}`);
    await page.getByRole("button", { name: /Pause/ }).click();
    await expect(page.getByText(/it has no operator key yet/)).toBeVisible();
    await page.getByTestId("make-key-send").filter({ hasText: "send pause" }).click();
    await expect.poll(() => sent.length).toBe(2);
    expect(made.n).toBe(1);
    expect(sent[1].operator).toMatch(/^owner#/);
    expect(sent[1].sig).toMatch(/^[0-9a-f]{128}$/);
    await expect(page.getByTestId("make-key-send")).toHaveCount(0);
    await expect.poll(async () => {
      const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as
        { node_id: string; desired: { state: { paused?: boolean } } | null }[];
      return all.find((n) => n.node_id === node)?.desired?.state.paused;
    }).toBe(true);
  });

  test("the pair pane says the refusal, and makes the key and sends", async ({ page, request }) => {
    const claude = "claude-code-example-3f9a21c0";
    await request.post(`${BACKEND}/v1/demo/seed-coding`);
    const pair = await (await request.post(`${BACKEND}/v1/pair`, { data: { node_id: claude } })).json() as { token: string };
    const { sent, made } = await keylessPlane(page, claude);
    await setConnection(page, { mode: "demo" });
    await goHash(page, `pair/${pair.token}`);
    const pane = page.getByTestId("pair-pane");
    await pane.getByTestId("pair-hold").click();
    await expect(pane.getByTestId("pair-hold-error")).toContainText("not sent: this workspace signs every command");
    await pane.getByTestId("make-key-send").click();
    await expect.poll(() => sent.length).toBe(2);
    expect(made.n).toBe(1);
    await expect(pane.getByTestId("pair-hold-error")).toHaveCount(0);
    await expect(pane.getByTestId("pair-hold")).toHaveText(/resume the agent|pause the agent/);
    await request.delete(`${BACKEND}/v1/pair/${pair.token}`);
  });

  test("Connect says the plane refuses unsigned commands where it does", async ({ page }) => {
    await page.route("**/v1/coding/operator-keys", (r) => r.fulfill({
      json: { keys: {}, fingerprint: "", unsigned_commands: false } }));
    await setConnection(page, { mode: "none" });
    await goHash(page, "connect?for=coding");
    const box = page.getByTestId("connect-unsigned");
    await expect(box).toContainText("refuses them");
    await expect(box).not.toContainText("signs nothing yet");
    await expect(box.getByTestId("make-key-send")).toHaveText("make this browser's operator key");
  });
});

/** A run uploaded for a node that never reports (the demo tree's, before
 *  QA-340): a trace, no heartbeat. */
async function uploadOnly(request: import("@playwright/test").APIRequestContext, node: string, run: string) {
  const ts = new Date().toISOString();
  const r = await request.post(`${BACKEND}/v1/ingest/${run}`, { data: { node_id: node, scenario: "governed-tree", events: [
    { schema_version: "1.0", seq: 0, node_id: node, kind: "tool_call", ts, causal_root: null, gate: null,
      verdict: "pass", payload: { tool: "web_search", args: { q: "x" } } },
  ] } });
  expect(r.ok()).toBeTruthy();
}

test("a node that never reported is offered no commands on its run page (QA-340)", async ({ page, request }) => {
  const node = `gov-tree-${hex6().slice(0, 4)}-orch`;
  const run = `govtree_${hex6()}`;
  await uploadOnly(request, node, run);
  let commands = 0;
  await page.route("**/v1/plane/*/command", (r) => { commands += 1; return r.continue(); });
  await setConnection(page, { mode: "adapter" });
  await goHash(page, `govern/run/${run}`);
  await expect(page.getByTestId("run-no-commands")).toContainText("never reported to the plane");
  await expect(page.getByTestId("run-controls")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Pause/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "test next run" })).toHaveCount(0);
  await expect(page.getByTestId("run-posture")).toContainText("not on the plane");
  expect(commands).toBe(0);
});
