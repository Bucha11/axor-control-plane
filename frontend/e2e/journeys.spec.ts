// Three ways in — coding agents, a production agent, both — in onboarding and
// in pricing, each with its own meter.
import { expect, test } from "@playwright/test";
import { BACKEND, goHash, seedNode, setConnection } from "./helpers";

test.describe("three ways in", () => {
  test("onboarding asks what you govern, and the coding path needs no tools", async ({ page }) => {
    await setConnection(page, { mode: "none" });
    await goHash(page, "connect");
    await expect(page.getByTestId("journey-chooser")).toBeVisible();
    await page.getByTestId("way-coding").click();
    await expect(page).toHaveURL(/#\/connect\?for=coding/);
    const install = page.getByTestId("cmd-install");
    await expect(install).toContainText("axor-wrap agents install claude-code");
    await expect(install).toContainText("axor-wrap agents install codex");
    await page.getByTestId("pick-codex").click();
    await expect(install).not.toContainText("codex");
    await expect(page.getByTestId("cmd-env")).toContainText("AXOR_PLANE_URL=");
    // no tool declaration, no base URL to repoint
    await expect(page.getByText("What tools does your agent use?")).toHaveCount(0);
  });

  test("a coding session appears on its own", async ({ page, request }) => {
    const node = `claude-code-e2ehost-${Date.now().toString(36).slice(-8)}`;
    await setConnection(page, { mode: "none" });
    await goHash(page, "connect?for=coding");
    await seedNode(request, node);
    await expect(page.getByTestId("coding-sessions")).toContainText("governed", { timeout: 10000 });
    await expect(page.getByTestId("coding-sessions")).toContainText(node);
  });

  test("both walks the coding path, then the production one", async ({ page }) => {
    await setConnection(page, { mode: "none" });
    await goHash(page, "connect");
    await page.getByTestId("way-both").click();
    await expect(page.getByTestId("coding-onboarding")).toBeVisible();
    await page.getByTestId("coding-done").click();
    await expect(page.getByText("What tools does your agent use?")).toBeVisible();
  });

  test("production goes straight to the proxy walkthrough", async ({ page }) => {
    await setConnection(page, { mode: "none" });
    await goHash(page, "connect?for=production");
    await expect(page.getByText("What tools does your agent use?")).toBeVisible();
  });

  test("pricing shows each way in with its own plans and meter", async ({ page }) => {
    await setConnection(page, { mode: "none" });
    await goHash(page, "pricing?for=coding");
    await expect(page.getByTestId("tier-coding")).toContainText("$25 / developer / mo");
    await expect(page.getByTestId("tier-team")).toHaveCount(0);
    await page.getByTestId("journey-production").click();
    await expect(page.getByTestId("tier-coding")).toHaveCount(0);
    await expect(page.getByTestId("tier-team")).toContainText("$299 / mo");
    await page.getByTestId("journey-both").click();
    await expect(page.getByTestId("tier-team")).toContainText("5 developer seats included");
    await expect(page.getByTestId("tier-security")).toContainText("25 developer seats included");
  });

  test("a coding session is not a governed node, and is counted as a developer", async ({ request }) => {
    const node = `codex-e2ehost-${Date.now().toString(36).slice(-8)}`;
    await seedNode(request, node);
    const before = await (await request.get(`${BACKEND}/v1/license/usage?months=1`)).json();
    const r = await request.post(`${BACKEND}/v1/plane/${node}/profile`,
      { data: { kind: "coding", agent: "codex", developer: "dev-e2e-1" } });
    expect(r.status()).toBe(204);
    const after = await (await request.get(`${BACKEND}/v1/license/usage?months=1`)).json();
    expect(after.months[0].active_developers).toBeGreaterThanOrEqual(1);
    expect(after.months[0].peak_nodes).toBeLessThanOrEqual(before.months[0].peak_nodes);
  });
});

test("on the connect page the header does not offer to connect, and a session connects", async ({ page, request }) => {
  await setConnection(page, { mode: "none" });
  await goHash(page, "connect?for=coding");
  await expect(page.getByTestId("coding-onboarding")).toBeVisible();
  await expect(page.getByRole("button", { name: "connect", exact: true })).toHaveCount(0);
  await seedNode(request, `claude-code-e2ehost-${Date.now().toString(36).slice(-8)}`);
  await expect(page.getByTestId("coding-sessions")).toContainText("governed", { timeout: 10000 });
  await expect(page.getByRole("button", { name: /connection:/ })).toBeVisible();
});

test("home: a coding tab and a production tab, and the coding example opens a tree", async ({ page }) => {
  await setConnection(page, { mode: "none" });
  await goHash(page, "home");
  await expect(page.getByTestId("coding-stage")).toBeVisible();
  await page.getByTestId("home-example-coding").click();
  await expect(page).toHaveURL(/#\/trees\/tree-example-coding/);
  await expect(page.getByTestId("tree-node-read")).toContainText("codex/luna");
  await expect(page.getByTestId("tree-item-read#3/f0")).toContainText("withheld · untrusted");
});

test("home: both is one link away", async ({ page }) => {
  await setConnection(page, { mode: "none" });
  await goHash(page, "home");
  await page.getByTestId("home-both").click();
  await expect(page).toHaveURL(/#\/connect\?for=both/);
});

test("a tree's agents fold into one row on Agents", async ({ page }) => {
  await setConnection(page, { mode: "none" });
  await goHash(page, "home");
  await page.getByTestId("home-example-coding").click();
  await expect(page).toHaveURL(/#\/trees\/tree-example-coding/);
  await goHash(page, "govern");
  const row = page.getByTestId("tree-row-tree-example-coding");
  await expect(row).toContainText("Add rate limiting to the auth module");
  await expect(page.getByTestId("agent-row-codex-example-r0")).toHaveCount(0);
  // the two loose sessions are not part of the tree
  await expect(page.getByTestId("agent-row-codex-example-77b0e4d2")).toBeVisible();
  await row.getByText("▸ show agents").click();
  await expect(page.getByTestId("agent-row-codex-example-r0")).toBeVisible();
  await row.getByRole("button", { name: "open tree →" }).click();
  await expect(page).toHaveURL(/#\/trees\/tree-example-coding/);
});
