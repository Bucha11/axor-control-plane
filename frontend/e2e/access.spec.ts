// What a role may do, as the client shows it, and how it treats a refusal:
// findings of the journaled QA run on 0.7.3 (QA-NNN in each test). The role is
// played by stubbing /v1/auth/status's scopes.
import { expect, test, type Page } from "@playwright/test";
import { BACKEND, goHash, seedNode, setConnection, uniqueNode } from "./helpers";

const VIEWER = ["read"];
const MEMBER = ["read", "ingest", "operate"];

async function playRole(page: Page, scopes: string[]): Promise<void> {
  await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
    auth_enabled: true, authenticated: true, scopes } }));
}

// a signed-in tab: its credentials live in this tab's session storage
async function signedIn(page: Page, role = "member"): Promise<void> {
  await page.addInitScript(() => {
    window.sessionStorage.setItem("axor-app", JSON.stringify({ version: 0, state: {
      apiToken: "acc-1", refreshToken: "ref-1", identityEmail: "ada@acme.io",
      vaultSigningToken: "", vaultCredsToken: "", guestTokens: [] } }));
  });
  await page.route("**/identity/v1/me", (r) => r.fulfill({ json: {
    user: { user_id: "u1", email: "ada@acme.io" }, active_org: "org_1", role,
    memberships: [{ org_id: "org_1", role, name: "Acme", tier: "free" }] } }));
  await page.route("**/identity/v1/orgs/org_1/members", (r) => r.fulfill({ json: { members: [
    { user_id: "u1", email: "ada@acme.io", role, since: "2026-11-01T00:00:00Z" },
    { user_id: "u0", email: "owner@acme.io", role: "owner", since: "2026-11-01T00:00:00Z" }] } }));
}

const ONE_INCIDENT = {
  items: [{ id: "ev:r1:0", kind: "dangerous", ts: new Date().toISOString(), node_id: "agent-1", run_id: "r1",
            seq: null, what: "send_email", detail: "", count: 1, status: "new" }],
  new: { made_up: 0, dangerous: 1, would_refuse: 0, refused: 0 }, needs_a_look: 1,
};

test.describe("access (QA, 0.7.3)", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
  });

  test("a scope refusal is one request, not a refresh and a resend, and says who can (QA-195, QA-019)", async ({ page }) => {
    // the page thinks it may (a member), the plane says the role is a viewer's now
    await playRole(page, MEMBER);
    await signedIn(page);
    await page.route("**/v1/incidents?*", (r) => r.fulfill({ json: ONE_INCIDENT }));
    let puts = 0;
    let refreshes = 0;
    await page.route("**/v1/incidents/status", (r) => {
      puts += 1;
      return r.fulfill({ status: 403, json: { error: "forbidden", need: "operate", have: ["read"] } });
    });
    await page.route("**/identity/v1/refresh", (r) => {
      refreshes += 1;
      return r.fulfill({ json: { access_token: "acc-2", refresh_token: "ref-2", user: { email: "ada@acme.io" } } });
    });
    await goHash(page, "evidence");
    await page.getByTestId("incident-status").selectOption("looked_at");
    await expect(page.getByTestId("incident-status-error"))
      .toHaveText("a viewer can't change this — an owner, admin or member can");
    await page.waitForTimeout(500);
    expect(puts).toBe(1);
    expect(refreshes).toBe(0);
  });

  test("a viewer gets no operator controls on an agent, and is told who can (QA-175)", async ({ page, request }) => {
    const node = uniqueNode("viewer-gov");
    await seedNode(request, node);
    await playRole(page, VIEWER);
    const commands: string[] = [];
    await page.route("**/v1/plane/*/command", (r) => { commands.push(r.request().url()); return r.continue(); });
    await goHash(page, "control");
    await page.getByTestId(`agent-row-${node}`).click();
    await expect(page.getByTestId("agent-viewer"))
      .toHaveText("as a viewer you can't pause, cap or stop agents — an owner, admin or member can");
    await expect(page.getByRole("button", { name: /^(Pause|Resume)$/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "more…" })).toHaveCount(0);
    await expect(page.getByLabel("budget")).toHaveCount(0);
    expect(commands).toEqual([]);
  });

  test("a viewer is not offered spawn or the example fleet (QA-152)", async ({ page }) => {
    await setConnection(page, { mode: "proxy" });
    await playRole(page, VIEWER);
    await goHash(page, "control");
    await page.getByText(/needs them wrapped in code/).waitFor();
    const fold = page.getByRole("button", { name: "example agents ▾" });
    if (await fold.isVisible()) await fold.click();
    await expect(page.getByTestId("spawn-viewer"))
      .toHaveText("as a viewer you can't add agents or examples — an owner, admin or member can");
    await expect(page.getByRole("button", { name: /Spawn a governed demo/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Load an example fleet" })).toHaveCount(0);
  });

  test("a viewer is offered neither arming a chaos test nor its schedule (QA-175, QA-152)", async ({ page }) => {
    await playRole(page, VIEWER);
    await page.route("**/v1/runs", (r) => r.fulfill({ json: [{ run_id: "r1", node_id: "agent-1", scenario: "live",
      intervened: false, completed: true, evidence: [], created_ts: "2026-11-27T10:00:00Z" }] }));
    await page.route("**/v1/chaos", (r) => r.fulfill({ json: { runs: [], modes: ["silent_fail"], schedule: {
      enabled: false, interval_hours: 24, nodes: [], modes: [], last_run_ts: null, last_armed: [], ee_active: true } } }));
    await goHash(page, "chaos");
    await expect(page.getByTestId("chaos-arm-viewer"))
      .toHaveText("as a viewer you can't break a tool on a live agent — an owner, admin or member can");
    await expect(page.getByTestId("chaos-arm")).toHaveCount(0);
    await expect(page.getByTestId("chaos-schedule-viewer"))
      .toHaveText("as a viewer you can't change the schedule — an owner, admin or member can");
    await expect(page.getByTestId("chaos-schedule-save")).toHaveCount(0);
    await expect(page.getByLabel("every how many hours")).toBeDisabled();
  });

  test("a viewer reads an incident's status and is not offered changing it (QA-196)", async ({ page }) => {
    await playRole(page, VIEWER);
    await page.route("**/v1/incidents?*", (r) => r.fulfill({ json: ONE_INCIDENT }));
    await goHash(page, "evidence");
    await expect(page.getByTestId("incidents-viewer"))
      .toHaveText("as a viewer you can't mark incidents — an owner, admin or member can");
    await expect(page.getByTestId("incident-status-text")).toHaveText("new");
    await expect(page.getByTestId("incident-status")).toHaveCount(0);
  });

  test("a viewer is offered neither share nor keep on a case, and the links are not asked for (QA-196, QA-214)", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await playRole(page, VIEWER);
    const linkReads: string[] = [];
    page.on("request", (r) => { if (r.url().includes("/v1/share-links")) linkReads.push(r.url()); });
    await goHash(page, "eval");
    await page.getByRole("button", { name: /Run the test/ }).click();
    await expect(page.getByTestId("case-viewer").first())
      .toHaveText("as a viewer you can't keep or share this case — an owner, admin or member can", { timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Share" })).toHaveCount(0);
    await expect(page.getByTestId("case-keep")).toHaveCount(0);
    expect(linkReads).toEqual([]);
  });

  test("on Replay a viewer is offered neither making the rule nor keeping the run (QA-196)", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-prod-incident`)).ok()).toBeTruthy();
    await playRole(page, VIEWER);
    const writes: string[] = [];
    page.on("request", (r) => { if (r.method() !== "GET" && /\/v1\/(pins|policy)/.test(r.url())) writes.push(r.url()); });
    await goHash(page, "explain/ex_prod_drop?seq=4&node=support-agent-prod");
    await page.getByTestId("whatif-outside").click();
    await expect(page.getByTestId("make-rule-viewer"))
      .toHaveText("as a viewer you can't make it a rule — an owner, admin or member can");
    await expect(page.getByTestId("make-rule-apply")).toHaveCount(0);
    await expect(page.getByTestId("replay-keep-viewer"))
      .toHaveText("as a viewer you can't keep runs as tests — an owner, admin or member can");
    await expect(page.getByTestId("replay-keep").getByRole("button")).toHaveCount(0);
    expect(writes).toEqual([]);
  });

  test("on Safe to ship? a viewer is offered neither unpin nor the schedule (QA-196, QA-152)", async ({ page }) => {
    await playRole(page, VIEWER);
    await page.route("**/v1/regression/schedule", (r) => r.fulfill({ json: {
      enabled: false, interval_hours: 24, last_run_ts: null, ee_active: true } }));
    await page.route("**/v1/regression", (r) => r.fulfill({ json: {
      rows: [{ run_id: "run_kept1", side: "must_block", label: "kept", result: "held", first_divergence: null,
               new_denial: null, pinned_denials: 1, escaped_denials: [], demo: false }],
      regressed: 0, escaped: 0, unanchored: 0, skipped: [], own_rows: 1, demo_rows: 0, safe_to_ship: true } }));
    await goHash(page, "prevent");
    await page.getByRole("button", { name: /Check these rules/ }).click();
    await expect(page.getByTestId("unpin-viewer"))
      .toHaveText("as a viewer you can't take a case out of the check — an owner, admin or member can");
    await page.getByTestId("case-run_kept1").click();
    await expect(page.getByTestId("unpin-run_kept1")).toHaveCount(0);
    await page.getByRole("button", { name: /run this check on a schedule/ }).click();
    await expect(page.getByTestId("schedule-viewer"))
      .toHaveText("as a viewer you can't change the schedule — an owner, admin or member can");
    await expect(page.getByRole("button", { name: /Schedule with the rules above/ })).toHaveCount(0);
  });
});
