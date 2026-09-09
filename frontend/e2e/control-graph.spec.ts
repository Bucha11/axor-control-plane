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

  test("the canned tree is reachable from the product, not only from a test", async ({ page }) => {
    // The backend route existed and nothing in the app called it: the lateral
    // hop and the undeclared foreign peer — edge kinds the live proxy spawn
    // does not produce — were seedable only by POSTing the endpoint by hand.
    // The button lives where the product puts its demo affordances: the screen
    // you see before Control has anything of yours on it.
    await setConnection(page, { mode: "proxy" });
    await goHash(page, "control");
    await page.getByRole("button", { name: /load the canned tree/ }).click();
    // Seeding from here also connects in adapter mode, so Control swaps to the
    // real view — the confirmation line belongs to the screen that stays.
    await page.getByRole("button", { name: "graph", exact: true }).click();
    for (const n of ["tree-orch", "tree-research", "tree-writer", "tree-scraper"]) {
      await expect(page.getByTestId(`topo-node-${n}`)).toBeVisible();
    }
    // the two things only this tree carries
    await expect(page.getByText("dashed = lateral (intra)")).toBeVisible();
    await expect(page.getByTestId("topo-node-partner-agent")).toBeVisible();
  });

  test("a traced tree is shown even when nothing is reporting to the plane", async ({ page }) => {
    // `/v1/plane/nodes` lists what heartbeats or has been commanded. A traced
    // tree's nodes are in neither, so Control answered "no governed nodes
    // connected yet" while holding the whole topology. The plane list is
    // stubbed because the suite shares one backend: other specs leave nodes on
    // it, and "nothing is reporting" is not otherwise reproducible here.
    const ctx = await request.newContext();
    await ctx.post(`${BACKEND}/v1/demo/seed-tree-run`);
    await ctx.dispose();
    await page.route("**/v1/plane/nodes", (route) =>
      route.fulfill({ json: [] }));

    await goHash(page, "control");
    await expect(page.getByText(/have traced here/)).toBeVisible();
    await expect(page.getByText(/No governed nodes connected yet/)).toHaveCount(0);
    await expect(page.getByTestId("topo-node-tree-orch")).toBeVisible();
    await expect(page.getByTestId("topo-node-partner-agent")).toBeVisible();
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
