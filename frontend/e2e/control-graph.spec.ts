// M2: the graph lens — Control's second mode (spec v2 Ch.1 §4). Topology is
// derived from traced spawn/message events (the seeded multi-agent tree run),
// never from self-reported parents.
import { expect, request, test } from "@playwright/test";
import { BACKEND, goHash, setConnection, uniqueNode } from "./helpers";

test.describe("control graph lens", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
  });

  test("the seeded tree renders as a graph with edge kinds", async ({ page }) => {
    const ctx = await request.newContext();
    await ctx.post(`${BACKEND}/v1/demo/seed-tree-run`);
    // one plane-connected node so Control has a live list to stand on
    const nid = uniqueNode("graph-live");
    await ctx.post(`${BACKEND}/v1/plane/${nid}/command`, {
      data: { version: 1, state: { paused: false } },
    });
    await ctx.dispose();

    await goHash(page, "control");
    await page.getByRole("button", { name: "graph", exact: true }).click();

    // the enclosure names the boundary; the four tree nodes render
    await expect(page.getByText("FEDERATION · your keyset")).toBeVisible();
    for (const n of ["tree-orch", "tree-research", "tree-writer", "tree-scraper"]) {
      await expect(page.getByTestId(`topo-node-${n}`)).toBeVisible();
    }
    // legend carries both intra edge kinds
    await expect(page.getByText("solid = delegation")).toBeVisible();
    await expect(page.getByText("dashed = lateral (intra)")).toBeVisible();

    // selection works from the graph
    await page.getByTestId("topo-node-tree-scraper").click();
    await expect(page.getByTestId("topology-graph")).toBeVisible();
  });

  test("list stays the default lens (quiet-until-wrong)", async ({ page }) => {
    const ctx = await request.newContext();
    const nid = uniqueNode("lens-default");
    await ctx.post(`${BACKEND}/v1/plane/${nid}/command`, {
      data: { version: 1, state: { paused: false } },
    });
    await ctx.dispose();

    await goHash(page, "control");
    await expect(page.getByText(nid)).toBeVisible();
    await expect(page.getByText("FEDERATION · your keyset")).not.toBeVisible();
  });
});
