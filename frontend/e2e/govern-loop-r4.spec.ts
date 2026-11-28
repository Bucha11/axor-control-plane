// Govern and the core loop (QA run 4, prod 0.7.4).
import { expect, test, type Page, type Route } from "@playwright/test";
import { BACKEND, goHash, seedNode, setConnection } from "./helpers";

const hex6 = () => Math.random().toString(16).slice(2, 8).padEnd(6, "0");

/** The plane refuses the first command for want of a key; /v1/operators
 *  registers one. Returns what was sent, and how many keys were made. */
async function keylessPlane(page: Page, node: string) {
  const sent: { operator: string; sig: string }[] = [];
  const made = { n: 0 };
  await page.route(`**/v1/plane/${node}/command`, async (r: Route) => {
    sent.push(r.request().postDataJSON());
    if (sent.length === 1) return r.fulfill({ status: 403, json: { detail: "no operator keys registered; commands rejected" } });
    return r.continue();
  });
  await page.route("**/v1/operators", async (r: Route) => {
    if (r.request().method() !== "POST") return r.continue();
    made.n += 1;
    const b = r.request().postDataJSON() as { pubkey: string };
    await r.fulfill({ status: 201, json: { operator_id: `owner#${b.pubkey.slice(0, 8)}` } });
  });
  return { sent, made };
}

test.describe("a command refused for want of an operator key (QA-501)", () => {
  test("the Agents panel makes the key in place, then sends the pause", async ({ page, request }) => {
    const node = `gov-key-${hex6()}`;
    await seedNode(request, node);
    const { sent, made } = await keylessPlane(page, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, `govern/${node}`);
    await page.getByRole("button", { name: /Pause/ }).click();
    await expect(page.getByText(/it has no operator key yet/)).toBeVisible();
    await page.getByTestId("make-key-send").filter({ hasText: "send pause" }).click();
    await expect.poll(() => sent.length).toBe(2);
    expect(made.n).toBe(1);
    expect(sent[1].operator).toMatch(/^owner#/);
    expect(sent[1].sig).toMatch(/^[0-9a-f]{128}$/);
    await expect(page.getByTestId("make-key-send")).toHaveCount(0);
    await expect.poll(async () => {
      const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as
        { node_id: string; desired: { state: { paused?: boolean } } | null }[];
      return all.find((n) => n.node_id === node)?.desired?.state.paused;
    }).toBe(true);
  });

  test("the pair pane says the refusal, and makes the key and sends", async ({ page, request }) => {
    const claude = "claude-code-example-3f9a21c0";
    await request.post(`${BACKEND}/v1/demo/seed-coding`);
    const pair = await (await request.post(`${BACKEND}/v1/pair`, { data: { node_id: claude } })).json() as { token: string };
    const { sent, made } = await keylessPlane(page, claude);
    await setConnection(page, { mode: "demo" });
    await goHash(page, `pair/${pair.token}`);
    const pane = page.getByTestId("pair-pane");
    await pane.getByTestId("pair-hold").click();
    await expect(pane.getByTestId("pair-hold-error")).toContainText("not sent: this workspace signs every command");
    await pane.getByTestId("make-key-send").click();
    await expect.poll(() => sent.length).toBe(2);
    expect(made.n).toBe(1);
    await expect(pane.getByTestId("pair-hold-error")).toHaveCount(0);
    await expect(pane.getByTestId("pair-hold")).toHaveText(/resume the agent|pause the agent/);
    await request.delete(`${BACKEND}/v1/pair/${pair.token}`);
  });

  test("Connect says the plane refuses unsigned commands where it does", async ({ page }) => {
    await page.route("**/v1/coding/operator-keys", (r) => r.fulfill({
      json: { keys: {}, fingerprint: "", unsigned_commands: false } }));
    await setConnection(page, { mode: "none" });
    await goHash(page, "connect?for=coding");
    const box = page.getByTestId("connect-unsigned");
    await expect(box).toContainText("refuses them");
    await expect(box).not.toContainText("signs nothing yet");
    await expect(box.getByTestId("make-key-send")).toHaveText("make this browser's operator key");
  });
});

/** A run uploaded for a node that never reports (the demo tree's, before
 *  QA-340): a trace, no heartbeat. */
async function uploadOnly(request: import("@playwright/test").APIRequestContext, node: string, run: string) {
  const ts = new Date().toISOString();
  const r = await request.post(`${BACKEND}/v1/ingest/${run}`, { data: { node_id: node, scenario: "governed-tree", events: [
    { schema_version: "1.0", seq: 0, node_id: node, kind: "tool_call", ts, causal_root: null, gate: null,
      verdict: "pass", payload: { tool: "web_search", args: { q: "x" } } },
  ] } });
  expect(r.ok()).toBeTruthy();
}

test("a node that never reported is offered no commands on its run page (QA-340)", async ({ page, request }) => {
  const node = `gov-tree-${hex6().slice(0, 4)}-orch`;
  const run = `govtree_${hex6()}`;
  await uploadOnly(request, node, run);
  let commands = 0;
  await page.route("**/v1/plane/*/command", (r) => { commands += 1; return r.continue(); });
  await setConnection(page, { mode: "adapter" });
  await goHash(page, `govern/run/${run}`);
  await expect(page.getByTestId("run-no-commands")).toContainText("never reported to the plane");
  await expect(page.getByTestId("run-controls")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Pause/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "test next run" })).toHaveCount(0);
  await expect(page.getByTestId("run-posture")).toContainText("not on the plane");
  expect(commands).toBe(0);
});

async function playRole(page: Page, scopes: string[]): Promise<void> {
  await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
    auth_enabled: true, authenticated: true, scopes } }));
}

/** A live node with a run that read text from outside: a cut is on offer. */
async function liveRun(request: import("@playwright/test").APIRequestContext, node: string, run: string) {
  await seedNode(request, node);
  const ts = new Date().toISOString();
  const r = await request.post(`${BACKEND}/v1/ingest/${run}`, { data: { node_id: node, scenario: "live", events: [
    { schema_version: "1.0", seq: 0, node_id: node, kind: "tool_call", ts, causal_root: null, gate: null,
      verdict: "pass", payload: { tool: "web_search", args: { q: "x" } } },
    { schema_version: "1.0", seq: 1, node_id: node, kind: "tool_result", ts, causal_root: null, gate: null,
      verdict: null, payload: { tool: "web_search", value_ref: "v_web", root: { sources: ["web"], sensitive: false } } },
  ] } });
  expect(r.ok()).toBeTruthy();
}

test("a viewer on a run page gets none of its controls, and is told who can (QA-342)", async ({ page, request }) => {
  const node = `gov-view-${hex6()}`;
  const run = `${node}-r1`;
  await liveRun(request, node, run);
  await playRole(page, ["read"]);
  const commands: string[] = [];
  await page.route("**/v1/plane/*/command", (r) => { commands.push(r.request().url()); return r.continue(); });
  await setConnection(page, { mode: "adapter", testBench: true });
  await goHash(page, `govern/run/${run}`);
  await expect(page.getByTestId("role-note")).toHaveText(/as a viewer you can't pause, stop, cap or test this agent/);
  await expect(page.getByTestId("run-timeline")).toContainText("web_search");
  await expect(page.getByTestId("run-controls")).toHaveCount(0);
  for (const name of [/^(Pause|Resume)/, /^Stop/, /^Inject/, "test next run", /remove this text/]) {
    await expect(page.getByRole("button", { name })).toHaveCount(0);
  }
  expect(commands).toEqual([]);
});

test("a demo node past its last heartbeat says it left, before any command (QA-347)", async ({ page, request }) => {
  const node = `governed-${hex6()}`;
  await seedNode(request, node);
  const ago = new Date(Date.now() - 120_000).toISOString();
  await page.route("**/v1/plane/nodes", async (route) => {
    const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as
      { node_id: string; reported: { updated_ts?: string } | null }[];
    for (const n of all) if (n.node_id === node && n.reported) n.reported.updated_ts = ago;
    await route.fulfill({ json: all });
  });
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "govern");
  await expect(page.getByTestId("fleet-count")).toBeVisible();
  // not a row, not counted, not "needs you"
  await expect(page.getByTestId(`agent-row-${node}`)).toHaveCount(0);
  await goHash(page, `govern/${node}`);
  await expect(page.getByTestId("demo-node-left")).toContainText("this demo node left the plane");
  await expect(page.getByRole("button", { name: /^(Pause|Resume)$/ })).toHaveCount(0);
  await expect(page.getByTestId("no-such-agent")).toHaveCount(0);
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("the recorded example tree takes no commands, and says so up front (QA-368)", async ({ page, request }) => {
  expect((await request.post(`${BACKEND}/v1/demo/seed-coding`)).ok()).toBeTruthy();
  const commands: string[] = [];
  await page.route("**/v1/plane/*/command", (r) => { commands.push(r.request().url()); return r.continue(); });
  await setConnection(page, { mode: "demo" });
  await goHash(page, "trees/tree-example-coding");
  await expect(page.getByTestId("example-intro")).toBeVisible();
  await expect(page.getByTestId("tree-example-no-commands")).toContainText("it takes no commands");
  await expect(page.getByTestId("tree-pause")).toHaveCount(0);
  await expect(page.getByTestId("tree-stop")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^cut/ })).toHaveCount(0);
  expect(commands).toEqual([]);
});

test.describe("the example fleet beside your own agents (QA-344, QA-370)", () => {
  test("is badged, not counted as live, takes no commands, and can be hidden there", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-fleet`)).ok()).toBeTruthy();
    const own = `gov-own-${hex6()}`;
    await seedNode(request, own);
    // the workspace's own agent next to the example: the plane still shows it
    await page.route("**/v1/plane/nodes", async (route) => {
      const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as
        { node_id: string; example?: boolean; example_hidden?: boolean }[];
      await route.fulfill({ json: all.map((n) => ({ ...n, example_hidden: false }))
        .filter((n) => n.example ? ["tree-orch", "tree-research", "tree-scraper", "tree-writer"].includes(n.node_id) : n.node_id === own) });
    });
    const commands: string[] = [];
    await page.route("**/v1/plane/*/command", (r) => { commands.push(r.request().url()); return r.continue(); });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "govern");
    await expect(page.getByTestId("example-badge-tree-writer")).toHaveText("example");
    await expect(page.getByTestId(`example-badge-${own}`)).toHaveCount(0);
    await expect(page.getByTestId("fleet-count")).toContainText("1 agent · live");
    await expect(page.getByTestId("fleet-count-examples")).toContainText("not counted");
    await page.getByTestId("agent-row-tree-writer").click();
    await expect(page.getByTestId("example-no-commands")).toContainText("example node — it takes no commands");
    await expect(page.getByRole("button", { name: /^(Pause|Resume)$/ })).toHaveCount(0);
    await page.getByTestId("agent-panel-close").click();
    await page.getByTestId("examples-hide").click();
    await expect(page.getByTestId("agent-row-tree-writer")).toHaveCount(0);
    await expect(page.getByTestId(`agent-row-${own}`)).toBeVisible();
    expect(commands).toEqual([]);
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });

  test("what is reporting leaves out the example by the plane's word, not its id", async ({ page, request }) => {
    const own = `gov-own-${hex6()}`;
    await seedNode(request, own);
    await page.route("**/v1/plane/nodes", async (route) => {
      const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as { node_id: string }[];
      await route.fulfill({ json: [
        ...all.filter((n) => n.node_id === own),
        // an example whose id says nothing of it
        { node_id: "tree-orch", example: true, example_hidden: false, desired: null, facts: [],
          reported: { applied_version: 0, level: "NORMAL", updated_ts: new Date().toISOString() }, profile: null },
      ] });
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "settings?tab=connect");
    const conn = page.getByTestId("connections");
    await expect(conn.getByTestId(`conn-${own}`)).toBeVisible();
    await expect(conn.getByTestId("conn-tree-orch")).toHaveCount(0);
    await expect(conn).toContainText("PRODUCTION AGENTS · 1");
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });

  test("\"production agents not shown\" leaves the example out (QA-370)", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-fleet`)).ok()).toBeTruthy();
    const prod = `gov-own-${hex6()}`;
    const coding = `claude-code-own-${hex6()}`;
    await seedNode(request, prod);
    await seedNode(request, coding);
    await request.post(`${BACKEND}/v1/plane/${coding}/profile`, { data: { kind: "coding", agent: "claude-code", developer: "dev-own" } });
    await page.route("**/v1/plane/nodes", async (route) => {
      const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as { node_id: string; example?: boolean }[];
      await route.fulfill({ json: all.map((n) => ({ ...n, example_hidden: false }))
        .filter((n) => n.example ? n.node_id.startsWith("tree-") : [prod, coding].includes(n.node_id)) });
    });
    await setConnection(page, { mode: "adapter" });
    await page.addInitScript(() => {
      const data = JSON.parse(localStorage.getItem("axor-app") ?? '{"state":{},"version":0}');
      data.state.journey = "coding";
      data.state.menuAll = false;
      localStorage.setItem("axor-app", JSON.stringify(data));
    });
    await goHash(page, "govern");
    await expect(page.getByTestId("agents-show-other")).toHaveText("1 production agent not shown");
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });
});

for (const [role, scopes, offered] of [["member", ["read", "ingest", "operate"], 1], ["viewer", ["read"], 0]] as const) {
  test(`#/health offers the example checks to a ${role}: ${offered ? "yes" : "no"} (QA-343)`, async ({ page, request }) => {
    const node = `gov-health-${hex6()}`;
    await seedNode(request, node);
    await playRole(page, [...scopes]);
    await page.route("**/v1/setup-health", (r) => r.fulfill({ json: { checks: [] } }));
    await page.route("**/v1/workspace", async (r) => {
      const body = await (await r.fetch()).json();
      await r.fulfill({ json: { ...body, work_started: false } });
    });
    await page.route("**/v1/plane/nodes", async (route) => {
      const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as { node_id: string }[];
      await route.fulfill({ json: all.filter((n) => n.node_id === node) });
    });
    const seeds: string[] = [];
    await page.route("**/v1/demo/seed-*", (r) => { seeds.push(r.request().url()); return r.continue(); });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, `health?node=${node}`);
    await expect(page.getByText("No health check yet.")).toBeVisible();
    await expect(page.getByTestId("setup-health-example")).toHaveCount(offered);
    await expect(page.getByTestId("health-example")).toHaveCount(offered);
    expect(seeds).toEqual([]);
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });
}

test("Free says what holds for governed nodes: more connect, demo nodes don't count (QA-360)", async ({ page }) => {
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "settings?tab=billing");
  const plan = page.getByTestId("billing-plan");
  await expect(plan).toContainText("Free");
  await expect(plan).toContainText("one governed node included (more still connect");
  await expect(plan).toContainText("demo nodes and examples don't count");
});

test.describe("the brake says what it holds, and whose (QA-361, QA-363)", () => {
  test("on the \"both\" journey the brake names coding agents", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-coding`)).ok()).toBeTruthy();
    await page.route("**/v1/license/entitlements", async (r) => {
      const body = await (await r.fetch()).json();
      await r.fulfill({ json: { ...body, free: true } });
    });
    await page.route("**/v1/coding/brake", (r) => r.fulfill({ json: { on: false } }));
    await setConnection(page, { mode: "demo" });
    await page.addInitScript(() => {
      const data = JSON.parse(localStorage.getItem("axor-app") ?? '{"state":{},"version":0}');
      data.state.journey = "both";
      localStorage.setItem("axor-app", JSON.stringify(data));
    });
    await goHash(page, "govern");
    await expect(page.getByTestId("brake-pull")).toHaveText("⏸ hold all your coding agents…");
  });

  test("a viewer reads the owner's agents are held, not \"of yours\"", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-coding`)).ok()).toBeTruthy();
    await page.addInitScript(() => {
      window.sessionStorage.setItem("axor-app", JSON.stringify({ version: 0, state: {
        apiToken: "acc-1", refreshToken: "ref-1", identityEmail: "viewer@acme.io",
        vaultSigningToken: "", vaultCredsToken: "", guestTokens: [] } }));
    });
    await page.route("**/identity/v1/me", (r) => r.fulfill({ json: {
      user: { user_id: "u1", email: "viewer@acme.io" }, active_org: "org_1", role: "viewer",
      memberships: [{ org_id: "org_1", role: "viewer", name: "Acme", tier: "free" }] } }));
    await page.route("**/identity/v1/orgs/org_1/members", (r) => r.fulfill({ json: { members: [] } }));
    await playRole(page, ["read"]);
    await page.route("**/v1/license/entitlements", async (r) => {
      const body = await (await r.fetch()).json();
      await r.fulfill({ json: { ...body, free: true } });
    });
    await page.route("**/v1/coding/brake", (r) => r.fulfill({ json: {
      on: true, by: "owner@acme.io", reason: "a skill gone bad", ts: new Date().toISOString() } }));
    await setConnection(page, { mode: "demo" });
    await goHash(page, "govern");
    const on = page.getByTestId("brake-on");
    await expect(on).toContainText("every one of the owner's coding agents is held — owner@acme.io");
    await expect(on).not.toContainText("of yours");
  });
});

test("a command lost to a concurrent write gets the command-lost note, not raw text (QA-362)", async ({ page, request }) => {
  const node = `gov-race-${hex6()}`;
  await seedNode(request, node);
  let calls = 0;
  await page.route(`**/v1/plane/${node}/command`, async (r) => {
    calls += 1;
    if (calls === 1) {
      return r.fulfill({ status: 409, json: { detail: `desired state for ${node} was changed concurrently 1 times running; the command was not applied` } });
    }
    return r.continue();
  });
  await setConnection(page, { mode: "adapter" });
  await goHash(page, `govern/${node}`);
  await page.getByRole("button", { name: /Pause/ }).click();
  const lost = page.getByTestId("command-lost");
  await expect(lost).toContainText("not sent: pause — someone else changed this agent first");
  await expect(page.getByText(/changed concurrently/)).toHaveCount(0);
});
