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
