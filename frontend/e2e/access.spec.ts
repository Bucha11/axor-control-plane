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
    // a run of the workspace's own agent (the example's events, recorded as
    // its own): on the example itself nothing is offered to anyone (QA-194)
    const node = `support-agent-view-${Date.now().toString(36)}`;
    const events = (await (await request.get(`${BACKEND}/v1/runs/ex_prod_drop/events`)).json()) as Record<string, unknown>[];
    const own = await request.post(`${BACKEND}/v1/ingest/${node}-drop`, { data: { node_id: node, scenario: "custom",
      events: events.map(({ id: _id, run_id: _run, ...e }) => ({ ...e, node_id: node })) } });
    expect(own.ok(), await own.text()).toBeTruthy();
    await playRole(page, VIEWER);
    const writes: string[] = [];
    page.on("request", (r) => { if (r.method() !== "GET" && /\/v1\/(pins|policy)/.test(r.url())) writes.push(r.url()); });
    await goHash(page, `explain/${node}-drop?seq=4&node=${node}`);
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

  test("a viewer gets no Skills \"turn off\" form, and is told who can (QA-213)", async ({ page }) => {
    await playRole(page, VIEWER);
    await page.route("**/v1/coding/skills", (r) => r.fulfill({ json: {
      off: [{ skill: "old-skill", ts: "2026-11-27T10:00:00Z", by: "ada@acme.io" }], attempts: {}, team: [] } }));
    await goHash(page, "skills");
    const panel = page.getByTestId("skills-off");
    await expect(panel.getByTestId("skills-off-viewer"))
      .toHaveText("as a viewer you can't turn a skill off for the team — an owner, admin or member can");
    await expect(panel.getByTestId("off-row-old-skill")).toBeVisible();
    await expect(panel.getByLabel("skill to turn off")).toHaveCount(0);
    await expect(panel.getByRole("button", { name: /turn off|turn back on/ })).toHaveCount(0);
  });

  test("a guest is not offered the audit tab (QA-238)", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => {
      const bearer = r.request().headers()["authorization"] ?? "";
      return r.fulfill({ json: bearer.includes("gs_")
        ? { auth_enabled: true, authenticated: true, guest: true, guest_sessions: true, scopes: MEMBER }
        : { auth_enabled: true, authenticated: false, guest_sessions: true, scopes: [] } });
    });
    await page.route("**/v1/auth/guest", (r) => r.fulfill({ status: 201, json: {
      token: "gs_e2e_guest_token", org: "guest_e2e", ttl_seconds: 1800,
      expires_at: new Date(Date.now() + 29 * 60_000).toISOString() } }));
    await goHash(page, "settings");
    await page.waitForFunction(() => (sessionStorage.getItem("axor-app") ?? "").includes("gs_e2e_guest_token"));
    await expect(page.getByTestId("settings-tab-alerts")).toBeVisible();
    await page.waitForTimeout(500);
    await expect(page.getByTestId("settings-tab-audit")).toHaveCount(0);
  });

  test("a visitor whose guest session was refused sees no self-hosted pricing copy (QA-232)", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
      auth_enabled: true, authenticated: false, guest_sessions: true, scopes: [] } }));
    await page.route("**/v1/auth/guest", (r) => r.fulfill({ status: 429, headers: { "retry-after": "60" },
      json: { detail: "too many guest sessions" } }));
    await page.route("**/v1/license/status", (r) => r.fulfill({ status: 401, json: { error: "unauthorized" } }));
    await goHash(page, "pricing?for=coding");
    const note = page.getByTestId("card-needs-org").first();
    await expect(note).toHaveText("sign in to a workspace to pay by card");
    await expect(page.locator("body")).not.toContainText("AXOR_");
  });

  test("a member or admin sees the license but is not offered pasting one (QA-064, QA-135)", async ({ page }) => {
    await playRole(page, MEMBER);
    await goHash(page, "settings");
    const fold = page.getByTestId("license-fold");
    await fold.locator("summary").click();
    await expect(fold.getByTestId("license-viewer"))
      .toHaveText("as a member or admin you can't apply a license — only the owner can");
    await expect(page.getByPlaceholder('{"license": {...}, "sig": "…"}')).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Verify license/ })).toHaveCount(0);
    await expect(fold).not.toContainText("AXOR_");
  });

  test("on Free the brake says it holds your agents, not the team's (product decision on T-GOV-18)", async ({ page, request }) => {
    expect((await request.post(`${BACKEND}/v1/demo/seed-coding`)).ok()).toBeTruthy();
    await page.route("**/v1/license/entitlements", async (r) => {
      const body = await (await r.fetch()).json();
      await r.fulfill({ json: { ...body, free: true } });
    });
    await page.route("**/v1/coding/brake", (r) => r.fulfill({ json: { on: false } }));
    await setConnection(page, { mode: "demo" });
    await goHash(page, "govern");
    await expect(page.getByTestId("brake-pull")).toHaveText("⏸ hold all your agents…");
  });

  test("a tab with no sign-in on a plane that needs one says so, and does not poll into 401s (QA-170)", async ({ page }) => {
    // credentials are per tab: a second tab of a signed-in browser has none
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
      auth_enabled: true, authenticated: false, guest_sessions: false, scopes: [] } }));
    const reads: string[] = [];
    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      if (path.startsWith("/v1/") && path !== "/v1/auth/status" && path !== "/v1/funnel") reads.push(path);
    });
    await goHash(page, "govern");
    await expect(page.getByTestId("tab-signed-out")).toContainText("this tab is signed out — sign in");
    await page.waitForTimeout(3000);
    expect(reads).toEqual([]);
  });

  test("a refused guest's screens wait instead of hammering the plane with 401s (QA-042, QA-214)", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: {
      auth_enabled: true, authenticated: false, guest_sessions: true, scopes: [] } }));
    await page.route("**/v1/auth/guest", (r) => r.fulfill({ status: 429, headers: { "retry-after": "60" },
      json: { detail: "too many guest sessions" } }));
    const reads: string[] = [];
    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      if (path.startsWith("/v1/") && !["/v1/auth/status", "/v1/auth/guest", "/v1/funnel"].includes(path)) reads.push(path);
    });
    await goHash(page, "govern");
    await expect(page.getByTestId("guest-busy")).toBeVisible();
    await page.waitForTimeout(3000);
    expect(reads).toEqual([]);
  });

  test("Security asks for no vault or share-link read the caller cannot have (QA-214, QA-042)", async ({ page }) => {
    const asked: string[] = [];
    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      if (r.method() === "GET" && (path.startsWith("/v1/vault/creds") || path.startsWith("/v1/vault/signing")
          || path === "/v1/share-links")) asked.push(path);
    });
    // a viewer: none of it is theirs
    await playRole(page, VIEWER);
    await goHash(page, "settings?tab=security");
    await expect(page.getByTestId("vault-viewer")).toBeVisible();
    await page.waitForTimeout(1500);
    expect(asked).toEqual([]);
  });

  test("the owner's Security asks for no vault read without the vault's own token (QA-214)", async ({ page }) => {
    await playRole(page, ["read", "ingest", "operate", "admin"]);
    await page.route("**/v1/security/posture", (r) => r.fulfill({ json: { items: [
      { id: "auth", ok: true, what: "every request needs a token or a sign-in", fix: "" },
      { id: "vault_creds_token", ok: true, what: "dispensing tool credentials needs its own token", fix: "" },
      { id: "vault_signing_token", ok: true, what: "requesting a signature needs its own token", fix: "" },
    ] } }));
    const asked: string[] = [];
    page.on("request", (r) => {
      const path = new URL(r.url()).pathname;
      if (r.method() === "GET" && (path.startsWith("/v1/vault/creds") || path.startsWith("/v1/vault/signing"))) asked.push(path);
    });
    await goHash(page, "settings?tab=security");
    await expect(page.getByTestId("tool-credentials")).toBeVisible();
    await page.waitForTimeout(1500);
    expect(asked).toEqual([]);
  });

  test("below Enterprise the audit tab does not ask for legal holds (QA-214)", async ({ page }) => {
    await page.route("**/v1/audit?*", (r) => r.fulfill({ json: { items: [], total: 0, actions: [] } }));
    let holds = 0;
    await page.route("**/v1/audit/holds", (r) => { holds += 1; return r.fulfill({ status: 402, json: {
      detail: { error: "plan_required", plan: "enterprise", detail: "on Enterprise" } } }); });
    await goHash(page, "settings?tab=audit");
    await expect(page.getByTestId("audit-pack-note")).toBeVisible();
    expect(holds).toBe(0);
  });

  test("a removed member's tab starts no guest and drops the workspace's data (QA-321, QA-322)", async ({ page }) => {
    let removed = false;
    let guestPosts = 0;
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: (r.request().headers()["authorization"] ?? "") && !removed
      ? { auth_enabled: true, authenticated: true, guest_sessions: true, scopes: MEMBER }
      : { auth_enabled: true, authenticated: false, guest_sessions: true, scopes: [] } }));
    await page.route("**/v1/auth/guest", (r) => {
      guestPosts += 1;
      return r.fulfill({ status: 201, json: { token: "gs_x", org: "guest_x", ttl_seconds: 1800,
        expires_at: new Date(Date.now() + 20 * 60_000).toISOString() } });
    });
    await signedIn(page);
    await page.route("**/identity/v1/refresh", (r) => r.fulfill({ status: 401,
      json: { detail: "you are no longer a member of Acme" } }));
    await page.route("**/v1/incidents?*", (r) => removed
      ? r.fulfill({ status: 401, json: { error: "unauthorized", detail: "no longer a member" } })
      : r.fulfill({ json: ONE_INCIDENT }));
    await page.route(/\/v1\/(runs|plane)/, (r) => removed
      ? r.fulfill({ status: 401, json: { error: "unauthorized", detail: "no longer a member" } })
      : r.fallback());
    await goHash(page, "evidence");
    await expect(page.getByText("send_email").first()).toBeVisible();
    removed = true;
    await goHash(page, "govern");
    await expect(page.getByTestId("session-ended")).toContainText("you are no longer a member of Acme");
    await page.waitForTimeout(1500);
    // QA-321: no guest session in its place
    expect(guestPosts).toBe(0);
    await goHash(page, "evidence");
    // QA-322: the workspace's incident is not shown from the cache
    await expect(page.getByText("send_email")).toHaveCount(0);
  });

  test("signing in, in a tab that was signed out, clears Policy's refusal (QA-481)", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: r.request().headers()["authorization"]
      ? { auth_enabled: true, authenticated: true, guest_sessions: false, scopes: ["read", "ingest", "operate", "admin"] }
      : { auth_enabled: true, authenticated: false, guest_sessions: false, scopes: [] } }));
    await page.route("**/identity/v1/login", (r) => r.fulfill({ json: {
      access_token: "acc-new", refresh_token: "ref-new", token_type: "Bearer", expires_in: 900,
      user: { user_id: "u1", email: "ada@acme.io" }, org: { org_id: "org_1", role: "owner", tier: "free" } } }));
    await goHash(page, "policy");
    await expect(page.getByTestId("policy-read-failed")).toContainText("signed out");
    await goHash(page, "settings?tab=workspace");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();
    await goHash(page, "policy");
    await expect(page.getByTestId("policy-read-failed")).toHaveCount(0);
  });

  test("the kernel badge appears once the tab signs in, without a reload (QA-462)", async ({ page }) => {
    await page.route("**/v1/auth/status", (r) => r.fulfill({ json: r.request().headers()["authorization"]
      ? { auth_enabled: true, authenticated: true, guest_sessions: false, scopes: ["read"] }
      : { auth_enabled: true, authenticated: false, guest_sessions: false, scopes: [] } }));
    await page.route("**/v1/version", (r) => r.request().headers()["authorization"]
      ? r.fulfill({ json: { versions: { "axor-core": "0.13.0", "axor-backend": "0.7.4" } } })
      : r.fulfill({ status: 401, json: { error: "unauthorized" } }));
    await page.route("**/identity/v1/login", (r) => r.fulfill({ json: {
      access_token: "acc-new", refresh_token: "ref-new", token_type: "Bearer", expires_in: 900,
      user: { user_id: "u1", email: "ada@acme.io" }, org: { org_id: "org_1", role: "owner", tier: "free" } } }));
    await goHash(page, "settings?tab=workspace");
    await expect(page.getByText("kernel 0.13.0")).toHaveCount(0);
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();
    await expect(page.getByText("kernel 0.13.0")).toBeVisible();
  });

});
