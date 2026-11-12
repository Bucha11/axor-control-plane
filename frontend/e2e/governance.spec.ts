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
    await expect(page.getByText(/needs the adapter/)).toBeVisible();
    await page.getByRole("button", { name: "Load an example fleet" }).first().click();
    await page.getByRole("button", { name: "graph", exact: true }).click();
    for (const n of ["tree-orch", "tree-research", "tree-writer", "tree-scraper"]) {
      await expect(page.getByTestId(`topo-node-${n}`)).toBeVisible();
    }
  });

  test("the agent list does not call a flagged or drifting agent healthy", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "govern");
    await expect(page.getByRole("heading", { name: /agents? needs? attention\./ })).toBeVisible();
    await expect(page.getByRole("button", { name: /probe · drift/ })).toBeVisible();
    await page.getByRole("button", { name: /sentinel · 1 flagged/ }).first().click();
    await expect(page).toHaveURL(/#\/sentinel\?node=/);
  });

  test("sentinel draws resources joined by the sessions that touched them", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "sentinel");
    await expect(page.getByRole("heading", { name: "What sentinel sees across sessions" })).toBeVisible();
    await expect(page.getByTestId("sentinel-graph")).toBeVisible();
    // the flagged resource is selected first, and its verdict reads as words
    const why = page.getByTestId("sentinel-why");
    await expect(why).toContainText("crm:customers");
    await expect(why).toContainText("flagged");
    await expect(why).toContainText(/staged in 4 tainted sessions/);
    await expect(why).toContainText(/export of it was denied at the sink in s-0419/);
    // a watched one, by click
    await page.getByTestId("sentinel-resource-s3:weekly-exports").click();
    await expect(why).toContainText(/not enough to flag, enough to watch/);
  });

  test("probe finds the drift, the cut is made, and the re-probe closes it", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "health");
    await page.getByLabel("health node").selectOption("tree-scraper");
    await expect(page.getByText("data_disclosure").first()).toBeVisible();
    await page.getByRole("button", { name: /Self-heal/ }).click();
    await page.getByPlaceholder(/reason \(required\)/).fill("web result #3 carries an injected instruction");
    await page.getByRole("button", { name: /excise & re-probe/ }).click();
    await expect(page.getByText(/awaiting the verifying/).first()).toBeVisible();
    await page.getByRole("button", { name: "run the example node's re-probe" }).click();
    await expect(page.getByText(/re-probe: OK/)).toBeVisible();
  });
});
