// M3: the multi-agent EvidenceCase — one case, anchored at the consequence,
// with a derive-on-open causal subgraph (spec v2 Ch.3). The size-1 render is
// pinned by golden-receipt.spec.ts; this spec pins the >1 branch.
import { expect, request, test } from "@playwright/test";
import { BACKEND, goHash, setConnection } from "./helpers";

test.describe("multi-agent EvidenceCase", () => {
  test("the tree case renders receipt + causal subgraph + influence", async ({ page }) => {
    const ctx = await request.newContext();
    await ctx.post(`${BACKEND}/v1/demo/seed-tree-run`);
    await ctx.dispose();

    await setConnection(page, { mode: "demo" });
    await goHash(page, "eval/ex_tree");

    // the receipt half — v0.13 fields, CONTAINED headline
    await expect(page.getByText("contained a fabrication at the boundary").first())
      .toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("WHAT HAPPENED").first()).toBeVisible();
    await expect(page.getByText("rates rose 0.25% (confirmed)").first()).toBeVisible();

    // the subgraph half — 3 causal nodes (writer excluded), roles, containment
    const sub = page.getByTestId("causal-subgraph");
    await expect(sub).toBeVisible();
    await expect(sub.getByText("CAUSAL SUBGRAPH · the 3 nodes that produced this claim")).toBeVisible();
    await expect(sub.getByText("CONTAINED · intra")).toBeVisible();
    await expect(sub.getByText("fault landed here")).toBeVisible();
    await expect(sub.getByText("denied propagation here")).toBeVisible();
    await expect(sub.getByText("tree-writer")).not.toBeVisible(); // causes, not org chart

    // influence ranking derives on demand (subgraph ablation)
    await sub.getByRole("button", { name: /influence ranking/ }).click();
    await expect(sub.getByText("v_sum")).toBeVisible();
    await expect(sub.getByText("ranked by subgraph ablation", { exact: false })).toBeVisible();
  });
});
