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
    await expect(page.getByTestId("cmd-install")).toContainText("uv tool install");
    // free tier, said before anything is installed
    await expect(page.getByTestId("coding-free")).toContainText("free for 1 developer");
    const install = page.getByTestId("cmd-repo");
    // one agent by default — the free plan; adding the second says it is the paid one
    await expect(install).toContainText("axor-wrap agents install claude-code");
    await expect(install).not.toContainText("codex");
    await expect(page.getByTestId("coding-both-note")).toHaveCount(0);
    await page.getByTestId("pick-codex").click();
    await expect(install).toContainText("axor-wrap agents install codex");
    await expect(page.getByTestId("coding-both-note")).toContainText("paid plan");
    // the machine connects with one command — no env vars to export
    await expect(page.getByTestId("cmd-connect")).toContainText("axor-wrap connect http");
    await expect(page.getByText(/AXOR_[A-Z_]+/)).toHaveCount(0);
    // this plane has no operator keys: it says so, and asks for no flag
    await expect(page.getByTestId("connect-unsigned")).toContainText("signs nothing yet");
    await expect(page.getByTestId("cmd-connect")).not.toContainText("--operator-key");
    // no tool declaration, no base URL to repoint
    await expect(page.getByText("What tools does your agent use?")).toHaveCount(0);
  });

  test("a plane with operator keys puts them into the connect command", async ({ page }) => {
    await setConnection(page, { mode: "none" });
    const hex = "ab".repeat(32);
    await page.route("**/v1/coding/operator-keys", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ keys: { op_ui: hex }, fingerprint: "1a2b 3c4d 5e6f 7a8b" }),
    }));
    await goHash(page, "connect");
    await page.getByTestId("way-coding").click();
    await expect(page.getByTestId("cmd-connect")).toContainText(`--operator-key op_ui=${hex}`);
    await expect(page.getByTestId("connect-signed")).toContainText("1a2b 3c4d 5e6f 7a8b");
  });

  test("with auth on, a signed-out visitor is asked to sign in before the key", async ({ page }) => {
    await setConnection(page, { mode: "none" });
    await page.route("**/v1/auth/status", (route) => route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ auth_enabled: true, authenticated: false, scopes: [] }),
    }));
    await goHash(page, "connect?for=coding");
    await expect(page.getByTestId("coding-signin")).toContainText("needs an account");
    await expect(page.getByTestId("mint-coding-key")).toHaveCount(0);
    await expect(page.getByTestId("cmd-connect")).toHaveCount(0);
  });

  test("a double-click on create the key makes one key", async ({ page }) => {
    // QA-045: the second click of a double-click got through before the button
    // turned disabled — two keys minted, the second secret never shown
    await setConnection(page, { mode: "none", apiToken: "ax_e2e_member" });
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: { auth_enabled: true, authenticated: true, scopes: ["read", "ingest"] } }));
    let posts = 0;
    await page.route("**/v1/keys/mine", async (r) => {
      if (r.request().method() !== "POST") return r.fulfill({ json: [] });
      posts += 1;
      await new Promise((ok) => setTimeout(ok, 300));
      await r.fulfill({ status: 201, json: { key_id: `k_${posts}`, secret: `ak_secret${posts}`, scopes: ["ingest"], node_id: null } });
    });
    await goHash(page, "connect?for=coding");
    await page.getByTestId("mint-coding-key").dblclick();
    await expect(page.getByTestId("cmd-connect")).toContainText("--key ak_secret1");
    expect(posts).toBe(1);
  });

  test("on an open plane no key is asked for", async ({ page }) => {
    // a fresh plane: no session has reported yet (other specs share this one)
    await page.route("**/v1/plane/nodes", (route) => route.fulfill({ json: [] }));
    await setConnection(page, { mode: "none" });
    await goHash(page, "connect?for=coding");
    await expect(page.getByText(/this plane is open — no key needed/)).toBeVisible();
    await expect(page.getByTestId("cmd-connect")).not.toContainText("--key");
    await expect(page.getByTestId("coding-skip")).toBeVisible();
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
    // a plane with nothing on it yet (other specs share this one)
    await page.route("**/v1/plane/nodes", (route) => route.fulfill({ json: [] }));
    await page.getByTestId("way-both").click();
    await expect(page.getByTestId("coding-onboarding")).toBeVisible();
    // one way in, in two parts — said, with the price of having both
    await expect(page.getByTestId("both-step-1")).toHaveAttribute("aria-current", "step");
    await expect(page.getByTestId("coding-free")).toContainText("$299 / month");
    await page.getByTestId("coding-done").click();
    await expect(page.getByTestId("both-production-intro")).toContainText("Now your production agent");
    await expect(page.getByTestId("prod-way-chooser")).toBeVisible();
    // moving on is not connecting: the header does not claim an adapter
    await expect(page.getByRole("button", { name: /connection: adapter/ })).toHaveCount(0);
    // and the first part is one click back
    await page.getByTestId("both-step-1").click();
    await expect(page.getByTestId("coding-onboarding")).toBeVisible();
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });

  test("production starts with the choice of how, told honestly", async ({ page }) => {
    await setConnection(page, { mode: "none" });
    await goHash(page, "connect?for=production");
    const compare = page.getByTestId("prod-way-compare");
    await expect(compare).toContainText("refuses a dangerous tool call");
    await expect(compare).toContainText("its tool calls; the agent still talks");
    await page.getByTestId("prod-way-proxy").click();
    // the proxy guards by default; a test run is the other mode
    await expect(page.getByTestId("guard-onboarding")).toBeVisible();
    await page.getByTestId("proxy-mode-test").click();
    await expect(page.getByText("What tools does your agent use?")).toBeVisible();
    await page.getByTestId("prod-way-back").click();
    await page.getByTestId("prod-way-code").click();
    await expect(page.getByTestId("adapter-onboarding")).toBeVisible();
  });

  test("pricing shows each way in with its own plans and meter", async ({ page }) => {
    await setConnection(page, { mode: "none" });
    await goHash(page, "pricing?for=coding");
    await expect(page.getByTestId("tier-coding")).toContainText("$25 / developer / mo");
    await expect(page.getByTestId("tier-team")).toHaveCount(0);
    // the coding tab talks coding: what is free, what is paid, and no Lab lines
    await expect(page.getByTestId("tier-community")).toContainText("Claude Code or Codex — one agent family");
    await expect(page.getByTestId("tier-community")).not.toContainText("EvidenceCase");
    await expect(page.getByTestId("tier-coding")).toContainText("one writes, the other reviews");
    await expect(page.getByText("Axor Security Platform")).toHaveCount(0);
    await page.getByTestId("journey-production").click();
    await expect(page.getByTestId("tier-coding")).toHaveCount(0);
    await expect(page.getByText("Axor Security Platform")).toBeVisible();
    await expect(page.getByTestId("tier-team")).toContainText("$299 / mo");
    await page.getByTestId("journey-both").click();
    // no rung includes seats: a developer is a Business seat on top of the
    // production rung, so Team reads as the production side and nothing else
    await expect(page.getByTestId("tier-team")).toContainText("Business seats on top, $40 each");
    await expect(page.getByTestId("tier-security")).toContainText("Business seats on top, $40 each");
    await expect(page.getByTestId("tier-team")).not.toContainText("seats included");
  });

  test("a coding session is not a governed node, and is counted as a developer", async ({ request }) => {
    const node = `codex-e2ehost-${Date.now().toString(36).slice(-8)}`;
    await seedNode(request, node);
    const before = await (await request.get(`${BACKEND}/v1/license/usage?months=1`)).json();
    const r = await request.post(`${BACKEND}/v1/plane/${node}/profile`,
      { data: { kind: "coding", agent: "codex", developer: "dev-e2e-1" } });
    expect(r.status()).toBe(200);
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

test("home: the coding example is one session first, then the next agent, then a tree", async ({ page }) => {
  await setConnection(page, { mode: "none" });
  await goHash(page, "home");
  await expect(page.getByTestId("coding-stage")).toBeVisible();
  await page.getByTestId("home-example-coding").click();
  await expect(page).toHaveURL(/#\/govern\/run\/ex_coding_claude$/);
  const story = page.getByTestId("run-example-story");
  await expect(story).toContainText("ONE CLAUDE CODE SESSION");
  await expect(page.getByTestId("run-timeline")).toContainText("text from outside");
  await story.getByTestId("run-example-govern").click();
  await expect(page).toHaveURL(/#\/govern\/run\/ex_coding_codex$/);
  await expect(page.getByTestId("refusal-reason")).toContainText("came from text from outside");
  await page.getByTestId("run-example-trees").click();
  await expect(page).toHaveURL(/#\/trees\/tree-example-coding/);
  await expect(page.getByTestId("tree-node-read")).toContainText("codex/luna");
  await expect(page.getByTestId("tree-item-read#3/f0")).toContainText("withheld · untrusted");
  // the example tells its story: who did what, and what was caught — each a link to its row
  const intro = page.getByTestId("example-intro");
  await expect(intro).toContainText("5 × Codex (luna), read-only, reports findings");
  await page.getByTestId("example-caught-read#1/f0").click();
  await expect(page.getByTestId("tree-item-read#1/f0")).toBeInViewport();
  await expect(intro).toContainText("withheld");
  // and it does not end in a dead end
  await page.getByTestId("example-connect").click();
  await expect(page).toHaveURL(/#\/connect\?for=coding/);
});

test("home: both is one link away, on the first screen of either tab", async ({ page }) => {
  await setConnection(page, { mode: "none" });
  await goHash(page, "home");
  await expect(page.getByTestId("home-both")).toBeInViewport();
  await page.getByTestId("home-tab-production").click();
  await expect(page.getByTestId("home-both")).toBeInViewport();
  await page.getByTestId("home-both").click();
  await expect(page).toHaveURL(/#\/connect\?for=both/);
});

test("a tree's agents fold into one row on Agents", async ({ page }) => {
  await setConnection(page, { mode: "none" });
  await goHash(page, "home");
  await page.getByTestId("home-example-coding").click();
  await expect(page).toHaveURL(/#\/govern\/run\/ex_coding_claude$/);
  await goHash(page, "govern");
  const row = page.getByTestId("tree-row-tree-example-coding");
  await expect(row).toContainText("Add rate limiting to the auth module");
  // its agents are in its row, never loose beside it
  await expect(page.locator('[data-tour="spawn"] > [data-testid="agent-row-codex-example-r0"]')).toHaveCount(0);
  // the two loose sessions are not part of the tree
  await expect(page.getByTestId("agent-row-codex-example-77b0e4d2")).toBeVisible();
  // the counters count rows: the tree is one, not one per agent in it
  // ...and an example's rows are said apart from the live agents (QA-344)
  await expect(page.getByTestId("fleet-count")).toContainText(/\d+ examples?, not counted/);
  await expect.poll(async () => {
    const chip = await page.getByTestId("status-chips").getByRole("button", { name: /^all/ }).innerText();
    const rows = await page.locator('[data-tour="spawn"] > [data-testid^="agent-row-"], [data-tour="spawn"] > [data-testid^="tree-row-"], [data-tour="spawn"] > [data-testid^="developer-row-"]').count();
    return Number(chip.replace(/\D+/g, "")) - rows;
  }).toBe(0);
  // a tree at work is open; a finished one opens on a click
  if (await row.getByText("▸ show agents").isVisible()) await row.getByText("▸ show agents").click();
  await expect(page.getByTestId("agent-row-codex-example-r0")).toBeVisible();
  await row.getByRole("button", { name: "open tree →" }).click();
  await expect(page).toHaveURL(/#\/trees\/tree-example-coding/);
});

test("past the free plan a session is kept but locked, and says what unlocks it", async ({ page, request }) => {
  const tag = Date.now().toString(36).slice(-6);
  const first = `claude-code-lock-a-${tag}`;
  const second = `claude-code-lock-b-${tag}`;
  for (const [node, dev] of [[first, `dev-lock-a-${tag}`], [second, `dev-lock-b-${tag}`]]) {
    await seedNode(request, node);
    const r = await request.post(`${BACKEND}/v1/plane/${node}/profile`,
      { data: { kind: "coding", agent: "claude-code", developer: dev } });
    expect(r.status()).toBe(200);
  }
  // whoever reported first in this workspace keeps the free seat: the second is locked
  const ent = await (await request.get(`${BACKEND}/v1/license/entitlements`)).json();
  expect(ent.locked[second]).toContain("developer");
  const cmd = await request.post(`${BACKEND}/v1/plane/${second}/command`,
    { data: { version: 1, operator: "op", state: { paused: true } } });
  expect(cmd.status()).toBe(402);

  await setConnection(page, { mode: "adapter" });
  await goHash(page, "govern");
  // a team's list is by person: the locked developer says so on their own row
  const person = page.getByTestId(`developer-row-dev-lock-b-${tag}`);
  await expect(person.getByTestId("plan-lock").first()).toContainText("second developer");
  await person.getByText("▸ show sessions").click();
  const row = page.getByTestId(`agent-row-${second}`);
  await expect(row.getByTestId("plan-lock")).toContainText("second developer");
  await row.click();
  await expect(page.getByTestId("plan-lock-card")).toContainText("still protected");
  await page.getByTestId("plan-lock-pricing").click();
  await expect(page).toHaveURL(/#\/pricing\?for=coding/);
});

test("the paid-plan notice folds, and a locked session stays out of the activity feed", async ({ page, request }) => {
  const tag = Date.now().toString(36).slice(-6);
  const first = `claude-code-fold-a-${tag}`;
  const second = `claude-code-fold-b-${tag}`;
  for (const [node, dev] of [[first, `dev-fold-a-${tag}`], [second, `dev-fold-b-${tag}`]]) {
    await seedNode(request, node);
    await request.post(`${BACKEND}/v1/plane/${node}/profile`,
      { data: { kind: "coding", agent: "claude-code", developer: dev } });
  }
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "govern");
  // the activity feed carries the open session's heartbeat run, never the locked one's
  await expect(page.getByTestId("fleet-activity")).not.toContainText(second);
  await page.getByTestId(`developer-row-dev-fold-b-${tag}`).getByText("▸ show sessions").click();
  await page.getByTestId(`agent-row-${second}`).click();
  const card = page.getByTestId("plan-lock-card");
  await expect(card).toContainText("still protected");
  await page.getByTestId("plan-lock-fold").click();
  await expect(card).not.toContainText("still protected");
  await expect(card).toContainText("paid plan · second developer");
  // remembered (the e2e helper rewrites storage on every load, so read it there)
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("axor-app")!).state.planNoticesFolded))
    .toBe(true);
  await page.getByTestId("plan-lock-fold").click();
  await expect(page.getByTestId("plan-lock-card")).toContainText("still protected");
});

test("Agents with only the coding example tells its story, in coding words", async ({ page, request }) => {
  await setConnection(page, { mode: "none" });
  await goHash(page, "home");
  await page.getByTestId("home-example-coding").click();
  await expect(page).toHaveURL(/#\/govern\/run\/ex_coding_claude$/);
  // other specs share this plane: show this page what a visitor's plane holds
  await page.route("**/v1/plane/nodes", async (route) => {
    const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as { node_id: string }[];
    await route.fulfill({
      json: all.filter((n) => /^(claude-code|codex)-example-|^tree-example/.test(n.node_id)) });
  });
  await goHash(page, "govern");
  const story = page.getByTestId("coding-example-story");
  await expect(story).toContainText("denied at the shell");
  // no adapter talk for a coding visitor, and the badge says what this is
  await expect(page.getByTestId("adapter-note")).toHaveCount(0);
  await expect(page.getByText(/needs them wrapped in code/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "connection: example data" })).toBeVisible();
  await page.getByTestId("story-codex").click();
  await expect(page.getByTestId("agent-panel")).toContainText(/codex-example-/);
  await story.getByTestId("story-connect").click();
  await expect(page).toHaveURL(/#\/connect\?for=coding/);
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("pricing for coding: the free plan's button connects coding agents", async ({ page }) => {
  await setConnection(page, { mode: "none" });
  await goHash(page, "pricing?for=coding");
  await page.getByTestId("cta-community").click();
  await expect(page).toHaveURL(/#\/connect\?for=coding/);
});

test("Agents with only the example fleet tells its story", async ({ page, request }) => {
  await setConnection(page, { mode: "none" });
  await goHash(page, "home");
  await page.getByTestId("home-tab-production").click();
  // other specs share this plane: show this page what a visitor's plane holds
  await page.route("**/v1/plane/nodes", async (route) => {
    const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as { node_id: string }[];
    const fleet = ["tree-orch", "tree-research", "tree-scraper", "tree-writer"];
    await route.fulfill({ json: all.filter((n) => fleet.includes(n.node_id)) });
  });
  await page.route("**/v1/plane/activity*", async (route) => {
    const res = await (await request.get(`${BACKEND}/v1/plane/activity?limit=200`)).json() as
      { events: { node_id: string }[] };
    await route.fulfill({ json: { ...res, events: res.events.filter((e) => /^tree-(orch|research|scraper|writer)$/.test(e.node_id)) } });
  });
  await page.getByTestId("home-example-fleet").click();
  const story = page.getByTestId("fleet-example-story");
  await expect(story).toContainText("a made-up answer, caught");
  await expect(page.getByText(/needs them wrapped in code/)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "connection: example data" })).toBeVisible();
  // the activity reads as messages between agents, not event kinds
  await expect(page.getByTestId("fleet-activity")).toContainText("sent a message to tree-research");
  await expect(page.getByTestId("fleet-activity")).not.toContainText("message_sent");
  await story.getByTestId("story-denied").click();
  await expect(page.getByTestId("agent-panel")).toContainText("tree-orch");
  await page.getByTestId("agent-panel-close").click();
  await page.getByTestId("story-graph").click();
  await expect(page.getByTestId("topo-node-tree-scraper")).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("the example asks a guest to sign up where it pays off, and does it for anyone else", async ({ page, request }) => {
  await page.route("**/v1/plane/nodes", async (route) => {
    const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as { node_id: string }[];
    const fleet = ["tree-orch", "tree-research", "tree-scraper", "tree-writer"];
    await route.fulfill({ json: all.filter((n) => fleet.includes(n.node_id)) });
  });
  // a guest: keeping the caught lie as a test is what an account is for
  await setConnection(page, { mode: "none", apiToken: "gs_e2e_guest" });
  await goHash(page, "home");
  await page.getByTestId("home-tab-production").click();
  await page.getByTestId("home-example-fleet").click();
  await expect(page.getByTestId("signup-own-agent")).toContainText("1 production agent");
  await page.getByTestId("story-keep").getByRole("button").click();
  const ask = page.getByTestId("account-required");
  await expect(ask).toContainText("Keep it as a test");
  await expect(ask).toContainText("before it ships");
  await ask.click({ position: { x: 5, y: 5 } });
  await page.getByTestId("story-alert").getByRole("button").click();
  await expect(page.getByTestId("account-required")).toContainText("Get an email when your agent goes wrong");
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("not a guest: keeping a caught lie as a test just keeps it", async ({ page, request }) => {
  await request.post(`${BACKEND}/v1/demo/seed-fleet`);
  await setConnection(page, { mode: "demo" });
  await goHash(page, "govern");
  await page.route("**/v1/plane/nodes", async (route) => {
    const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as { node_id: string }[];
    const fleet = ["tree-orch", "tree-research", "tree-scraper", "tree-writer"];
    await route.fulfill({ json: all.filter((n) => fleet.includes(n.node_id)) });
  });
  // the regression corpus is shared by every spec: answer the pin here rather
  // than add an escaped exfil to everyone else's counts
  let pinned: unknown = null;
  await page.route("**/v1/pins/ex_tree", async (route) => {
    pinned = route.request().postDataJSON();
    await route.fulfill({ json: { pinned: "ex_tree" } });
  });
  await page.reload();
  await expect(page.getByTestId("signup-own-agent")).toHaveCount(0);
  await page.getByTestId("story-keep").getByRole("button").click();
  await expect(page.getByTestId("story-keep")).toContainText("kept as a test");
  expect(pinned).toMatchObject({ side: "must_block" });
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("an incident's replay opens where the agent said it", async ({ page, request }) => {
  await request.post(`${BACKEND}/v1/demo/seed-coding`);
  await setConnection(page, { mode: "demo" });
  await goHash(page, "explain/ex_coding_claude");
  const step = page.getByTestId("replay-step");
  await expect(step).toContainText("The agent said");
  await expect(page.getByTestId("replay-to-incident")).toContainText(/it said so at step \d+/);
  // this run read a web page: whether other rules change it is a fair question here
  await expect(page.getByTestId("replay-whatif")).toBeVisible();
});
