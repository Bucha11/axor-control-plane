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

test("after the first demo node the other examples are still one click away (QA-174)", async ({ page, request }) => {
  await seedNode(request, `governed-${hex6()}`);
  // spawning switched this browser to the adapter: the fold above the list is gone
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "govern");
  const more = page.getByTestId("more-examples");
  await more.getByRole("button", { name: "try more examples ▾" }).click();
  await expect(more.getByRole("button", { name: "Spawn a governed demo node" })).toBeVisible();
  await expect(more.getByRole("button", { name: "Spawn a governed demo TREE" })).toBeVisible();
  await expect(more.getByRole("button", { name: /load the canned tree/ })).toBeVisible();
});

test("a lie caught by a chaos test is not offered as keepable (QA-193)", async ({ page, request }) => {
  const node = `chaos-lie-${hex6()}`;
  const run = `run_${hex6()}`;
  const l = (seq: number, kind: string, payload: object) => ({
    schema_version: "1.0", seq, node_id: node, kind, ts: new Date().toISOString(),
    causal_root: null, gate: null, verdict: null, payload });
  expect((await request.post(`${BACKEND}/v1/ingest/${run}`, { data: { node_id: node, scenario: "tool-deprivation", events: [
    l(0, "tool_call", { tool: "web_search", method: "GET" }),
    l(1, "fault_injected", { tool: "web_search", mode: "silent_fail", canary: "" }),
    l(2, "tool_result", { tool: "web_search", status: 500, faulted: true }),
    l(3, "claim", { text: "Based on the tool results, the answer is confirmed.", structured: true,
                    claims: { tools_succeeded: ["web_search"], tools_used: ["web_search"] } }),
  ] } })).ok()).toBeTruthy();
  await setConnection(page, { mode: "adapter" });
  await goHash(page, `catch/${run}`);
  await expect(page.getByText(/the same break can be run again from Chaos tests/)).toBeVisible();
  await expect(page.getByText("and it can become a test.")).toHaveCount(0);
  await goHash(page, "evidence");
  const row = page.locator('[data-kind="made_up"]').filter({ hasText: node });
  await expect(row.getByTestId("incident-status")).toBeVisible();
  const options = await row.getByTestId("incident-status").locator("option").allTextContents();
  expect(options).toContain("not a problem");
  expect(options).not.toContain("kept as a test");
  expect(options).not.toContain("made a rule");
});

test.describe("operator keys (QA-173)", () => {
  test("a command signed with a revoked key says the key was revoked", async ({ page, request }) => {
    const node = `gov-revoked-${hex6()}`;
    await seedNode(request, node);
    await page.route(`**/v1/plane/${node}/command`, (r) =>
      r.fulfill({ status: 403, json: { detail: "operator key 'ada@acme.io#377c9673' was revoked" } }));
    await setConnection(page, { mode: "adapter" });
    await goHash(page, `govern/${node}`);
    await page.getByRole("button", { name: /Pause/ }).click();
    await expect(page.getByText(/operator key was revoked/).first()).toBeVisible();
    await expect(page.getByText(/not one the workspace knows/)).toHaveCount(0);
  });

  test("a vouch from a revoked key is not shown as a vouch", async ({ page }) => {
    const session = {
      access_token: "acc-token", refresh_token: "ref-token", token_type: "Bearer", expires_in: 900,
      user: { user_id: "usr_1", email: "ada@acme.io" }, org: { org_id: "org_1", role: "owner", tier: "community" },
    };
    await setConnection(page, { mode: "adapter" });
    await page.route("**/identity/v1/login", (r) => r.fulfill({ json: session }));
    await page.route("**/identity/v1/me", (r) => r.fulfill({ json: {
      user: session.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }] } }));
    await page.route("**/identity/v1/orgs/org_1/members", (r) => r.fulfill({ json: { members: [] } }));
    const key = (id: string, revoked: string | null, by: string | null) => ({
      operator_id: id, org: "org_1", email: id.split("#")[0], pubkey: "ab".repeat(32), label: "", created_ts: "t",
      revoked_ts: revoked, endorsement: by ? { payload: {}, operator: by, sig: "00" } : null });
    await page.route("**/v1/operators", (r) => r.fulfill({ json: { operators: [
      key("ada@acme.io#377c9673", "2026-11-27T19:50:00Z", null),
      key("sam@acme.io#77a9023b", null, "ada@acme.io#377c9673"),
    ] } }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    const panel = page.getByTestId("operator-keys");
    await expect(panel.getByTestId("op-key-vouch-revoked")).toContainText("whose key was revoked");
    await expect(panel.getByText(/^vouched for by ada@acme.io#377c9673$/)).toHaveCount(0);
  });
});

test("a command another tab beat to the agent is named and can be sent again (QA-172)", async ({ page, request }) => {
  const node = `gov-race-${hex6()}`;
  await seedNode(request, node);
  let calls = 0;
  await page.route(`**/v1/plane/${node}/command`, async (r) => {
    calls += 1;
    if (calls === 1) return r.fulfill({ status: 409, json: { detail: "stale version 1; expected 2" } });
    return r.continue();
  });
  await setConnection(page, { mode: "adapter" });
  await goHash(page, `govern/${node}`);
  await page.getByRole("button", { name: /Pause/ }).click();
  const lost = page.getByTestId("command-lost");
  await expect(lost).toContainText("not sent: pause — someone else changed this agent first");
  await expect(page.getByText(/stale version/)).toHaveCount(0);
  await lost.getByRole("button", { name: "send pause again" }).click();
  await expect(lost).toHaveCount(0);
  await expect.poll(async () => {
    const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as
      { node_id: string; desired: { state: { paused?: boolean } } | null }[];
    return all.find((n) => n.node_id === node)?.desired?.state.paused;
  }).toBe(true);
});

test("an address naming no agent says there is none (QA-176)", async ({ page, request }) => {
  await seedNode(request, `gov-real-${hex6()}`);
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "govern/no-such-node-qa");
  await expect(page.getByTestId("no-such-agent")).toContainText("There is no agent “no-such-node-qa” on the plane.");
});

test.describe("a skill too large to check (QA-217)", () => {
  test("is refused in words before anything is sent", async ({ page }) => {
    let sent = 0;
    await page.route("**/v1/wrap/skill-check", (r) => { sent += 1; return r.continue(); });
    await goHash(page, "skills");
    await page.getByTestId("skill-files").setInputFiles({
      name: "SKILL.md", mimeType: "text/markdown", buffer: Buffer.alloc(5_400_000, "a") });
    await expect(page.getByText(/too large to check here: the file is 5\.4 MB/)).toBeVisible();
    expect(sent).toBe(0);
  });

  test("a web server's 413 page is said in words, not shown", async ({ page }) => {
    await page.route("**/v1/wrap/skill-check", (r) => r.fulfill({ status: 413, contentType: "text/html",
      body: "<html><head><title>413 Request Entity Too Large</title></head><body><center><h1>413 Request Entity Too Large</h1></center><hr><center>nginx</center></body></html>" }));
    await goHash(page, "skills");
    await page.getByTestId("skill-files").setInputFiles({
      name: "SKILL.md", mimeType: "text/markdown", buffer: Buffer.from("# a skill\n") });
    await expect(page.getByText(/too large for the plane to take in one request/)).toBeVisible();
    await expect(page.getByText(/Request Entity Too Large|<html>/)).toHaveCount(0);
  });
});
