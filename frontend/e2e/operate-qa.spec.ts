// Operating screens: findings of the 2026-11-27 QA run on 0.7.2.
import { expect, test } from "@playwright/test";
import { BACKEND, answerAsk, goHash, seedNode, setConnection, uniqueNode } from "./helpers";

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

test.describe("operator keys (QA-083)", () => {
  test("a browser with no key sends no made-up operator, and is told it has no key", async ({ page, request }) => {
    const node = uniqueNode("gov-nokey");
    await seedNode(request, node);
    let body: { operator?: string } = {};
    await page.route(`**/v1/plane/${node}/command`, (r) => {
      body = r.request().postDataJSON();
      return r.fulfill({ status: 403, json: { detail: `unknown operator '${body.operator}'` } });
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await page.getByTestId(`agent-row-${node}`).click();
    await page.getByRole("button", { name: /Pause/ }).click();
    await expect(page.getByText(/this browser has no operator key/).first()).toBeVisible();
    await expect(page.getByText(/Settings → workspace → operator keys/).first()).toBeVisible();
    expect(body.operator).toBe("");
  });

  test("a replan records the operator whose key signs it", async ({ page, request }) => {
    const node = uniqueNode("gov-replan");
    await seedNode(request, node);
    let body: { operator?: string; state?: { replan?: { operator?: string } } } = {};
    await page.route(`**/v1/plane/${node}/command`, (r) => {
      body = r.request().postDataJSON();
      return r.fulfill({ status: 403, json: { detail: "sig_invalid" } });
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await page.evaluate(async () => {
      const k = await import("/src/opKey.ts");
      await k.create();
      await k.setOperatorId("ada@acme.io#qa083");
    });
    await page.getByTestId(`agent-row-${node}`).click();
    await page.getByRole("button", { name: "more…" }).click();
    await page.getByRole("button", { name: /Replan/ }).click();
    await answerAsk(page, "the plan read an untrusted page");
    await expect.poll(() => body.operator).toBe("ada@acme.io#qa083");
    expect(body.state?.replan?.operator).toBe("ada@acme.io#qa083");
  });

  test("Settings calls them operator keys, as a refused command does", async ({ page }) => {
    const ok = (body: unknown) => (route: import("@playwright/test").Route) => route.fulfill({ json: body });
    const user = { user_id: "usr_1", email: "ada@acme.io" };
    await setConnection(page, { mode: "adapter" });
    await page.route("**/identity/v1/login", ok({
      access_token: "acc-token", refresh_token: "ref-token", token_type: "Bearer", expires_in: 900,
      user, org: { org_id: "org_1", role: "owner", tier: "community" } }));
    await page.route("**/identity/v1/me", ok({ user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }] }));
    await page.route("**/identity/v1/orgs/org_1/members", ok({ members: [] }));
    await page.route("**/v1/operators", ok({ operators: [] }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    const panel = page.getByTestId("operator-keys");
    await expect(panel).toContainText("operator keys");
    await expect(panel).not.toContainText("signing keys");
  });
});

test("a cap is a whole number in range, and says so (QA-084, QA-034)", async ({ page, request }) => {
  const node = uniqueNode("gov-capint");
  await seedNode(request, node);
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "control");
  await page.getByTestId(`agent-row-${node}`).click();
  const input = page.getByPlaceholder("set / lower");
  const apply = page.getByRole("button", { name: /apply/ });
  for (const bad of ["1.5", "12345678901234567890123", "1e309", "abc"]) {
    await input.fill(bad);
    await expect(page.getByText(/cap must be a whole number of calls from 0 to 1,000,000/), bad).toBeVisible();
    await expect(apply, bad).toBeDisabled();
  }
  await input.fill("1");
  await expect(page.getByText(/cap must be a whole number/)).toHaveCount(0);
  await apply.click();
  await expect(page.getByTestId("ask")).toContainText(`Cap ${node} at 1 tool call?`);
  await answerAsk(page);
  await expect(page.getByTestId("agent-panel")).toContainText(/budget cap\s*1 call(?!s)/);
});

test("the example test opened by its address runs, as the Chaos link does (QA-086)", async ({ page }) => {
  // a fresh browser: nothing connected yet
  await goHash(page, "catch?auto=1");
  await expect(page.getByText("fabricated a tool result").first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Not connected.")).toHaveCount(0);
});

test("Replay's search boxes have a limit that says so, and a failed search reads (QA-088)", async ({ page }) => {
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "explain");
  const box = page.getByLabel("contains");
  await box.fill("Ж🚀".repeat(1700));
  expect((await box.inputValue()).length).toBeLessThanOrEqual(200);
  await expect(page.getByTestId("search-text-limit")).toContainText("at most 200 characters");
  // a search that gets no answer at all
  await page.route("**/v1/runs/search**", (r) => r.abort("connectionclosed"));
  await box.fill("DROP TABLE");
  await expect(page.getByTestId("search-error")).toContainText("the search did not reach the server");
  await expect(page.getByText("Failed to fetch")).toHaveCount(0);
});

test.describe("Replay addresses (QA-089)", () => {
  test("a cursor the run does not have opens the nearest step and says so", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-adapter-runs`)).ok()).toBeTruthy();
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "explain/ex_block?cursor=-3");
    await expect(page.getByText(/STEP 1 OF \d+/)).toBeVisible();
    await expect(page.getByTestId("replay-cursor-missing")).toContainText("no step at position -3 — opened at step 1");
    await expect(page).toHaveURL(/cursor=0/);
  });

  test("a bad run id is asked for once, encoded once", async ({ page }) => {
    const asked: string[] = [];
    page.on("request", (r) => { if (r.url().includes("/v1/replay/")) asked.push(r.url()); });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "explain/no%20such%20run");
    await expect(page.getByTestId("replay-title")).toContainText("no such run");
    await page.waitForTimeout(2500);   // a retry would have gone by now
    expect(asked).toHaveLength(1);
    expect(asked[0]).toContain("/v1/replay/no%20such%20run");
  });
});

test("the proxy's test run keeps its setup and tools across a reload; a long name is refused (QA-050, QA-051)", async ({ page, request }) => {
  await request.put(`${BACKEND}/v1/policy`, { data: { tools: {} } });
  await goHash(page, "connect?for=production");
  await page.getByTestId("prod-way-proxy").click();
  await page.getByTestId("proxy-mode-test").click();
  const name = page.getByPlaceholder("tool name ⏎");
  const url = page.getByPlaceholder("https://endpoint ⏎");
  await name.fill("x".repeat(5000));
  await url.fill("https://long.example");
  await url.press("Enter");
  await expect(page.getByTestId("proxy-add-error")).toContainText("a tool name is at most 64 characters (this one has 5000)");
  await name.fill("kept_tool");
  await url.fill("https://kept.example/api");
  await url.press("Enter");
  await expect(page.getByTestId("proxy-add-error")).toHaveCount(0);
  await page.reload();
  await expect(page.getByTestId("proxy-mode-test")).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("https://kept.example/api")).toBeVisible();
  await expect(page.getByTestId("prod-way-chooser")).toHaveCount(0);
  await request.put(`${BACKEND}/v1/policy`, { data: { tools: {} } });
});

test.describe("signing in (QA-052, QA-053)", () => {
  const ok = (body: unknown) => (route: import("@playwright/test").Route) => route.fulfill({ json: body });
  const user = { user_id: "usr_1", email: "ada.lovelace.long.address@acme-analytical-engines.io" };

  test("the account menu's log out sits on one line, at 1366 and at 390", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await page.route("**/identity/v1/login", ok({
      access_token: "acc-token", refresh_token: "ref-token", token_type: "Bearer", expires_in: 900,
      user, org: { org_id: "org_1", role: "owner", tier: "community" } }));
    await page.route("**/identity/v1/me", ok({ user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }] }));
    await page.route("**/identity/v1/orgs/org_1/members", ok({ members: [] }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill(user.email);
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await goHash(page, "govern");
    for (const width of [1366, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await page.getByRole("button", { name: user.email }).click();
      const out = page.getByRole("dialog", { name: "account" }).getByRole("button", { name: "log out" });
      await expect(out).toBeVisible();
      const box = (await out.boundingBox())!;
      expect(box.height, `log out at ${width}`).toBeLessThan(28);
      await page.keyboard.press("Escape");
      await goHash(page, width === 1366 ? "evidence" : "govern");
    }
  });

  test("a log in with an address that is not one says so", async ({ page }) => {
    let sent = 0;
    await page.route("**/identity/v1/login", (r) => { sent += 1; return r.fulfill({ status: 401, json: {} }); });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("<img src=x onerror=alert(1)>@example.com");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByText("that is not an e-mail address we can send to")).toBeVisible();
    expect(sent).toBe(0);
  });
});

test.describe("operating screens say what is going on (QA-090, QA-091)", () => {
  test("an example node commanded says it takes no commands", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-fleet`)).ok()).toBeTruthy();
    // a pause sent to it earlier: commanded, never applied
    await page.route("**/v1/plane/nodes", async (route) => {
      const all = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json() as
        { node_id: string; example?: boolean; desired: unknown; reported: { applied_version?: number } | null }[];
      for (const n of all) {
        if (n.node_id === "tree-writer") n.desired = { version: 99, state: { paused: true } };
      }
      await route.fulfill({ json: all });
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "govern/tree-writer");
    await expect(page.getByTestId("example-no-commands")).toContainText("example node — it takes no commands");
    await expect(page.getByText(/applying pause/)).toHaveCount(0);
    await page.unrouteAll({ behavior: "ignoreErrors" });
  });

  test("inject, when it is off, says why", async ({ page, request }) => {
    const node = uniqueNode("gov-inject");
    await seedNode(request, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await page.getByTestId(`agent-row-${node}`).click();
    await page.getByRole("button", { name: "more…" }).click();
    await expect(page.getByRole("button", { name: /Inject next turn/ })).toBeDisabled();
    await expect(page.getByTestId("inject-why")).toContainText("test-bench agents only");
  });

  test("the governed demo's incident is the demo's, not a chaos test", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-tree-run`)).ok()).toBeTruthy();
    await page.route("**/v1/incidents**", async (route) => {
      const body = await (await route.fetch()).json();
      body.items = body.items.slice(0, 1).map((i: Record<string, unknown>) => ({ ...i, scenario: "governed" }));
      await route.fulfill({ json: body });
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "evidence");
    const list = page.getByTestId("evidence-list");
    await expect(list).toContainText("the governed demo");
    await expect(list).not.toContainText("a chaos test");
  });

  test("the header says example data for the example, whichever link switched the connection", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "evidence");
    await expect(page.getByRole("button", { name: "connection: example data" })).toBeVisible();
    await expect(page.getByText("demo-mode · mock tools")).toHaveCount(0);
  });

  test("at 390 the case's buttons and the cap field fit their labels", async ({ page, request }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await goHash(page, "catch?auto=1");
    const replay = page.getByRole("button", { name: /Replay this moment/ });
    await expect(replay).toBeVisible({ timeout: 20_000 });
    for (const b of [replay, page.getByTestId("case-export")]) {
      expect((await b.boundingBox())!.height).toBeLessThan(30);
    }
    const node = uniqueNode("gov-cap390");
    await seedNode(request, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, `govern/${node}`);
    const cap = page.getByPlaceholder("set / lower");
    await expect(cap).toBeVisible();
    const fits = await cap.evaluate((el: HTMLInputElement) => {
      const s = getComputedStyle(el);
      const probe = document.createElement("span");
      probe.style.font = s.font;
      probe.textContent = el.placeholder;
      document.body.appendChild(probe);
      const need = probe.getBoundingClientRect().width;
      probe.remove();
      return need <= el.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
    });
    expect(fits).toBe(true);
  });
});

test("the agent map names a spawned tree's agents in full, over the edges (QA-033)", async ({ page }) => {
  const ids = ["gov-tree-3914-orch", "gov-tree-3914-research", "gov-tree-3914-scraper"];
  await page.route("**/v1/plane/nodes", (r) => r.fulfill({ json: [] }));
  await page.route("**/v1/plane/topology", (r) => r.fulfill({ json: {
    nodes: ids.map((node_id) => ({ node_id, kind: "self" })),
    edges: [
      { from: ids[0], to: ids[1], kind: "delegation", messages: 1, denied: 0, last_gate: null },
      { from: ids[1], to: ids[2], kind: "delegation", messages: 1, denied: 0, last_gate: null },
      { from: ids[2], to: ids[0], kind: "lateral", messages: 1, denied: 1, last_gate: "taint_floor" },
    ] } }));
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "control");
  for (const id of ids) {
    const label = page.getByTestId(`topo-node-${id}`).locator("text").first();
    await expect(label).toBeVisible();
    // the drawn text, not its <title>
    expect(await label.evaluate((el) => [...el.childNodes].filter((n) => n.nodeType === 3)
      .map((n) => n.textContent).join(""))).toBe(id);
    // drawn with a halo of the panel's colour: a line under it does not cross its letters
    expect(await label.evaluate((el) => getComputedStyle(el).paintOrder)).toMatch(/^stroke/);
  }
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

// QA-104, QA-106, QA-107: cramped after the spacing change
test.describe("layout at 390 and 1366 (QA-104, QA-106, QA-107)", () => {
  const lines = (el: import("@playwright/test").Locator) => el.evaluate((e) => {
    const r = document.createRange();
    r.selectNodeContents(e);
    return new Set([...r.getClientRects()].map((x) => Math.round(x.top))).size;
  });

  test("Incidents: the buttons stay inside the card and on one line", async ({ page, request }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    expect((await request.post(`${BACKEND}/v1/demo/seed-coding`)).ok()).toBeTruthy();
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "evidence");
    const card = page.getByTestId("evidence-list").locator(":scope > [data-kind]").first();
    await expect(card).toBeVisible();
    const replay = card.getByRole("button", { name: /replay it|where it came from/ });
    const c = (await card.boundingBox())!;
    const b = (await replay.boundingBox())!;
    expect(b.x + b.width).toBeLessThanOrEqual(c.x + c.width - 8);
    expect(b.height).toBeLessThan(30);
  });

  test("Skills: a dry run's date stays on one line", async ({ page, request }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    expect((await request.post(`${BACKEND}/v1/demo/seed-skill-try`)).ok()).toBeTruthy();
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "skills");
    const when = page.getByTestId("dry-run-release-helper").locator("span", { hasText: /^\d{4}-\d\d-\d\d \d\d:\d\d$/ });
    await expect(when).toBeVisible();
    expect(await lines(when)).toBe(1);
  });

  test("Skill evals: 'times each' stays on one line", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/v1/machines", (r) => r.fulfill({ json: { machines: [{ machine_id: "m-laptop", host: "laptop",
      repo_label: "acme/api", agents: ["claude-code"], last_seen: new Date().toISOString(), online: true, yours: true }] } }));
    await page.route("**/v1/machine-jobs?limit=10", (r) => r.fulfill({ json: { jobs: [] } }));
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "skill-evals");
    const label = page.locator("label", { hasText: "times each" }).locator("span").first();
    await expect(label).toHaveText("times each");
    expect(await lines(label)).toBe(1);
  });

  test("Team memory: the rotation sentence goes on after its select", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-memory`)).ok()).toBeTruthy();
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "memory");
    const select = page.getByLabel("freshness window");
    await expect(select).toBeVisible();
    // the words after the select start on the select's line
    const after = await page.getByTestId("memory-fresh").evaluate((sec) => {
      const sel = sec.querySelector("select")!;
      let n: Node | null = sel.nextSibling;
      while (n && !(n.textContent ?? "").includes("is no longer given")) n = n.nextSibling;
      const r = document.createRange();
      r.selectNodeContents(n!);
      return { text: r.getClientRects()[0].top, select: sel.getBoundingClientRect() };
    });
    expect(after.text).toBeLessThan(after.select.bottom);
    expect(after.text).toBeGreaterThanOrEqual(after.select.top - 12);
  });

  test("Home: the coding example's record box holds its label", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await goHash(page, "home");
    await page.getByTestId("home-tab-coding").click();
    const sub = page.locator("svg text", { hasText: "setup.limitkit.example” ← web" });
    await expect(sub).toBeVisible({ timeout: 20_000 });
    const box = sub.locator("xpath=preceding-sibling::*[local-name()='rect'][1]");
    const t = (await sub.boundingBox())!;
    const r = (await box.boundingBox())!;
    expect(t.x).toBeGreaterThanOrEqual(r.x);
    expect(t.x + t.width).toBeLessThanOrEqual(r.x + r.width);
  });
});
