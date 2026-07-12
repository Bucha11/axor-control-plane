// M4: the two-tree containment view — the multi-agent hero (spec v2 Ch.2 §3,
// decision v2-18). Containment is event-grounded; the systemic outcome is a
// label pair, never a governance-attributed number.
import { expect, request, test } from "@playwright/test";
import { BACKEND, goHash, setConnection } from "./helpers";

test.describe("two-tree containment", () => {
  test("the tree case shows both worlds, the ratio, and the label pair", async ({ page }) => {
    const ctx = await request.newContext();
    await ctx.post(`${BACKEND}/v1/demo/seed-tree-run`);
    await ctx.dispose();

    await setConnection(page, { mode: "demo" });
    await goHash(page, "eval/ex_tree");

    const tt = page.getByTestId("two-tree");
    await expect(tt).toBeVisible({ timeout: 15_000 });
    await expect(tt.getByText("UNGOVERNED")).toBeVisible();
    await expect(tt.getByText("GOVERNED", { exact: true })).toBeVisible();

    // event-grounded headline: 1 gated boundary, 1 held
    await expect(tt.getByText("1/1 boundaries held")).toBeVisible();
    // intra hops are informational — carried, never counted
    await expect(tt.getByText("carried, not laundered").first()).toBeVisible();
    await expect(tt.getByText("DENIED — contained here")).toBeVisible();

    // the strongest honest claim, as labels
    await expect(tt.getByText("fabricated_failure")).toBeVisible();
    await expect(tt.getByText("honest_failure")).toBeVisible();

    // the recording plays to the divergence
    await tt.getByRole("button", { name: /Run the recording/ }).click();
    await expect(tt.getByText("FABRICATION ESCAPED")).toBeVisible({ timeout: 15_000 });
    await expect(tt.getByText("CONTAINED", { exact: true })).toBeVisible();
  });
});
