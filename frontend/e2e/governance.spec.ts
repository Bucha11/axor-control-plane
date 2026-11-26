// Agents beyond the node list: what sentinel saw across sessions, and the
// probe health check with the context repair it proposes. Before the example
// fleet both were empty on any deployment without a real adapter running
// sentinel and probe, so governance was the one part of the product a visitor
// could not see.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe.serial("governance on the example fleet", () => {
  test("Agents is open without an adapter, and loads an example fleet", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "govern");
    await expect(page.getByText(/needs them wrapped in code/)).toBeVisible();
    // other specs may have put agents on this plane: then the examples are
    // folded — and when one of them already loaded the example fleet (its
    // tree is today's, QA-039), the page offers no second load
    const fold = page.getByRole("button", { name: "example agents ▾" });
    const load = page.getByRole("button", { name: "Load an example fleet" }).first();
    const tree = page.getByText("tree-orch").first();
    await expect(fold.or(load).or(tree)).toBeVisible();
    if (await fold.isVisible()) await fold.click();
    if (await load.isVisible()) await load.click();
    await page.getByRole("button", { name: "graph", exact: true }).click();
    for (const n of ["tree-orch", "tree-research", "tree-writer", "tree-scraper"]) {
      await expect(page.getByTestId(`topo-node-${n}`)).toBeVisible();
    }
  });

  test("the agent list does not call a flagged or drifting agent healthy", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "govern");
    await expect(page.getByRole("heading", { name: /agents? needs? attention\./ })).toBeVisible();
    // other specs share this backend and may leave a drifting node of their own
    await expect(page.getByRole("button", { name: /health · drifted/ }).first()).toBeVisible();
    await page.getByRole("button", { name: /reputation · 1 flagged/ }).first().click();
    await expect(page).toHaveURL(/#\/sentinel\?node=/);
  });

  test("sentinel draws resources joined by the sessions that touched them", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "sentinel");
    await expect(page.getByRole("heading", { name: "Data an agent touched, across sessions" })).toBeVisible();
    await expect(page.getByTestId("sentinel-graph")).toBeVisible();
    // the flagged resource is selected first, and its verdict reads as words
    const why = page.getByTestId("sentinel-why");
    await expect(why).toContainText("crm:customers");
    await expect(why).toContainText("flagged");
    await expect(why).toContainText(/copied a little at a time in 4 sessions that had read outside text/);
    await expect(why).toContainText(/sending it out was refused in s-0419/);
    // a watched one, by click
    await page.getByTestId("sentinel-resource-s3:weekly-exports").click();
    await expect(why).toContainText(/not enough to flag, enough to watch/);
  });

  test("probe finds the drift, the cut is made, and the re-probe closes it", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "health");
    await page.getByLabel("health node").selectOption("tree-scraper");
    await expect(page.getByText("giving out data").first()).toBeVisible();
    await page.getByRole("button", { name: /Remove the cause/ }).click();
    await page.getByPlaceholder(/why \(required\)/).fill("web result #3 carries an injected instruction");
    await page.getByRole("button", { name: /remove and check again/ }).click();
    await expect(page.getByText(/waiting for the agent's next check/).first()).toBeVisible();
    await page.getByRole("button", { name: "check the example agent again" }).click();
    await expect(page.getByText(/checked again: answers as before/)).toBeVisible();
  });
});
