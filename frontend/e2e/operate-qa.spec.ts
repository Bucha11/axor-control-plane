// Operating screens: findings of the 2026-11-27 QA run on 0.7.2.
import { expect, test } from "@playwright/test";
import { BACKEND, goHash, setConnection } from "./helpers";

test.describe("times on the operating screens", () => {
  test.use({ timezoneId: "Asia/Kathmandu" });   // +05:45: never equal to UTC

  test("a stamp that is not ISO is never shown as a date (QA-081), one format (QA-105)", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-tree-run`)).ok()).toBeTruthy();
    await page.route("**/v1/incidents**", async (route) => {
      const body = await (await route.fetch()).json();
      body.items[0].ts = "seq:3";
      for (const it of body.items.slice(1)) it.ts = "2026-11-27T10:00:05+00:00";
      await route.fulfill({ json: body });
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "evidence");
    const list = page.getByTestId("evidence-list");
    await expect(list.locator(":scope > [data-kind]").first()).toBeVisible();
    await expect(list).not.toContainText(/2000|2001/);
    await expect(list.locator(":scope > [data-kind]").first()).toContainText("seq:3");
    // the viewer's local time, as everywhere else: 10:00:05 UTC is 15:45:05 here
    await expect(list.locator(":scope > [data-kind]").nth(1)).toContainText("2026-11-27 15:45:05");
    await expect(list).not.toContainText(/\d+\/\d+\/\d{4}/);
  });

  test("the activity feed shows no fake date and reads newest first (QA-081, QA-103)", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-fleet`)).ok()).toBeTruthy();
    expect((await request.post(`${BACKEND}/v1/demo/seed-tree-run`)).ok()).toBeTruthy();
    const now = Date.now();
    await page.route("**/v1/plane/activity", async (route) => {
      const body = await (await route.fetch()).json();
      const ev = body.events.slice(0, 4);
      // arrival order is not time order: an example seeded after real work
      ev[0].ts = new Date(now - 3 * 3600_000).toISOString();
      ev[1].ts = "seq:2";
      ev[2].ts = new Date(now - 60_000).toISOString();
      ev[3].ts = new Date(now - 2 * 3600_000).toISOString();
      for (const e of ev) e.kind = "node_spawned";
      ev.forEach((e: { run_id: string; payload: Record<string, unknown> }, i: number) => {
        e.run_id = `r${i}`;
        e.payload = { role: `role${i}` };
      });
      await route.fulfill({ json: { events: ev } });
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    const rows = page.getByTestId("fleet-activity").getByTestId("feed-row");
    await expect(rows).toHaveCount(4);
    await expect(rows.nth(0)).toContainText("role2");
    await expect(rows.nth(1)).toContainText("role3");
    await expect(rows.nth(2)).toContainText("role0");
    await expect(rows.nth(3)).toContainText("role1");
    await expect(page.getByTestId("fleet-activity")).not.toContainText(/2000|2001|\b(Sat|Mon|Thu)\b/);
  });
});

// other specs share this plane: show the page only what a visitor's plane holds
async function onlyTheExampleFleet(page: import("@playwright/test").Page,
                                   request: import("@playwright/test").APIRequestContext) {
  const fleet = ["tree-orch", "tree-research", "tree-scraper", "tree-writer"];
  await page.route("**/v1/plane/nodes", async (route) => {
    const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as { node_id: string }[];
    await route.fulfill({ json: all.filter((n) => fleet.includes(n.node_id)) });
  });
  await page.route("**/v1/plane/topology", async (route) => {
    const t = await (await request.get(`${BACKEND}/v1/plane/topology`)).json() as
      { nodes: { node_id: string; kind: string }[]; edges: { from: string; to: string }[] };
    const keep = (id: string) => fleet.includes(id) || id === "partner-agent";
    await route.fulfill({ json: { ...t, nodes: t.nodes.filter((n) => keep(n.node_id)),
                                  edges: t.edges.filter((e) => keep(e.from) && keep(e.to)) } });
  });
}

test("the example fleet keeps the spawn actions and can be hidden (QA-082)", async ({ page, request }) => {
  expect((await request.post(`${BACKEND}/v1/demo/seed-fleet`)).ok()).toBeTruthy();
  await onlyTheExampleFleet(page, request);
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "control");
  const story = page.getByTestId("fleet-example-story");
  await expect(story).toBeVisible();
  await expect(page.getByRole("button", { name: "Spawn a governed demo node" })).toBeVisible();
  await expect(page.getByRole("button", { name: /load the canned tree/ })).toBeVisible();

  await story.getByTestId("story-hide").click();
  await expect(story).toHaveCount(0);
  await expect(page.getByTestId("agent-row-tree-orch")).toHaveCount(0);
  await expect(page.getByTestId("topo-node-tree-orch")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Spawn a governed demo node" })).toBeVisible();
  // hidden for good (the store keeps it), until asked for again
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("axor-app")!).state.fleetExampleHidden)).toBe(true);
  await page.getByRole("button", { name: "Load an example fleet" }).click();
  await expect(page.getByTestId("fleet-example-story")).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});
