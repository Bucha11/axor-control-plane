// The real multi-node source (spec v2 Ch.4, M1 item 6): the proxy spawns a
// 3-node governed tree — real IntentLoops over the axor-core message bus —
// and the whole v2 surface lights up from its trace: topology graph, causal
// subgraph, containment. No canned verdicts anywhere in this path.
import { expect, request, test } from "@playwright/test";
import { BACKEND, goHash, setConnection, uniqueNode } from "./helpers";

const PROXY = "http://127.0.0.1:8401";

test.describe("governed tree (real runtime path)", () => {
  test("spawn → topology graph → contained case", async ({ page }) => {
    const ctx = await request.newContext();
    const spawned = await ctx.post(`${PROXY}/axor/governed/spawn-tree`);
    expect(spawned.ok()).toBeTruthy();
    const { run_id, nodes, denials } = await spawned.json();
    expect(denials).toBe(1); // the orchestrator's own IntentLoop denied
    // one plane-connected node so Control has a live list (lens toggle) to stand on
    const nid = uniqueNode("tree-live");
    await ctx.post(`${BACKEND}/v1/plane/${nid}/command`, {
      data: { version: 1, state: { paused: false } },
    });
    await ctx.dispose();

    // the real tree renders in the graph lens
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await page.getByRole("button", { name: "graph", exact: true }).click();
    for (const nid of Object.values(nodes) as string[]) {
      await expect(page.getByTestId(`topo-node-${nid}`)).toBeVisible();
    }

    // the CONTAINED case renders with its causal subgraph, all three nodes
    await setConnection(page, { mode: "demo" });
    await goHash(page, `eval/${run_id}`);
    await expect(page.getByText("contained a fabrication at the boundary").first())
      .toBeVisible({ timeout: 15_000 });
    const sub = page.getByTestId("causal-subgraph");
    await expect(sub).toBeVisible();
    await expect(sub.getByText("CONTAINED · intra")).toBeVisible();
  });
});
