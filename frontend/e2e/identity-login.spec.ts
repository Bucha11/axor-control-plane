// The axor-identity sign-in panel in Settings. The e2e stack runs the backend
// and proxy but not the identity service, so /identity/* is stubbed per test —
// this exercises the panel's UI behavior (sign in, error, sign up, log out),
// not the identity service itself (that has its own suite).
import { expect, test } from "@playwright/test";
import { answerAsk, goHash, setConnection } from "./helpers";

const SESSION = {
  access_token: "acc-token",
  refresh_token: "ref-token",
  token_type: "Bearer",
  expires_in: 900,
  user: { user_id: "usr_1", email: "ada@acme.io" },
  org: { org_id: "org_1", role: "owner", tier: "community" },
};

function jsonRoute(status: number, body: unknown) {
  return (route: import("@playwright/test").Route) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

test.describe("identity login (Settings)", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
  });

  test("signing in shows the signed-in state", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await goHash(page, "settings");
    await page.getByLabel("email").fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();
    await expect(page.getByRole("button", { name: "log out" })).toBeVisible();
  });

  test("a wrong password surfaces the server error", async ({ page }) => {
    await page.route(
      "**/identity/v1/login",
      jsonRoute(401, { detail: "invalid email or password" }),
    );
    await goHash(page, "settings");
    await page.getByLabel("email").fill("ada@acme.io");
    await page.getByLabel("password").fill("nope");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByText("invalid email or password")).toBeVisible();
    // still on the form
    await expect(page.getByLabel("email")).toBeVisible();
  });

  test("signing up creates a workspace and signs in", async ({ page }) => {
    await page.route("**/identity/v1/signup", jsonRoute(201, SESSION));
    await goHash(page, "settings");
    await page.getByRole("button", { name: "new? create a workspace" }).click();
    await expect(page.getByLabel("organization name")).toBeVisible();
    await page.getByLabel("email").fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByLabel("organization name").fill("Acme");
    await page.getByRole("button", { name: "create workspace" }).click();
    await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();
  });

  test("a sign-up error goes once the field it was about is changed", async ({ page }) => {
    // QA-016: "the password is too short" stayed on screen after the password was fixed
    await page.route("**/identity/v1/signup",
      jsonRoute(400, { detail: "the password is too short (at least 8 characters)" }));
    await goHash(page, "settings");
    await page.getByRole("button", { name: "new? create a workspace" }).click();
    await page.getByLabel("email").fill("ada@acme.io");
    await page.getByLabel("password").fill("abc");
    await page.getByLabel("organization name").fill("Acme");
    await page.getByRole("button", { name: "create workspace" }).click();
    await expect(page.getByText("the password is too short")).toBeVisible();
    await page.getByLabel("password").fill("correct horse");
    await expect(page.getByText("the password is too short")).toHaveCount(0);
  });

  test("logging out returns to the sign-in form", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await goHash(page, "settings");
    await page.getByLabel("email").fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByRole("button", { name: "log out" })).toBeVisible();
    await page.getByRole("button", { name: "log out" }).click();
    await expect(page.getByLabel("email")).toBeVisible();
    await expect(page.getByRole("button", { name: "log in", exact: true })).toBeVisible();
  });

  test("an admin sees the team and invites someone with a link", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user,
      active_org: "org_1",
      role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }],
    }));
    await page.route("**/identity/v1/orgs/org_1/members", jsonRoute(200, { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" }] }));
    let made: unknown = null;
    await page.route("**/identity/v1/orgs/org_1/invites", async (route) => {
      if (route.request().method() === "POST") {
        made = route.request().postDataJSON();
        await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({
          invite_id: "inv_1", token: "tok-abc", role: "viewer", email: "bob@acme.io",
          expires_ts: "2026-12-01T00:00:00Z", mailed: false }) });
      } else {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ invites: [] }) });
      }
    });
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByText("workspace Acme")).toBeVisible();
    await expect(page.getByTestId("team")).toContainText("ada@acme.io");
    await page.getByLabel("invite email").fill("bob@acme.io");
    await page.getByLabel("invite role").selectOption("viewer");
    await page.getByTestId("invite-make").click();
    await expect(page.getByTestId("invite-made")).toContainText("#/invite/tok-abc");
    expect(made).toMatchObject({ email: "bob@acme.io", role: "viewer" });
  });

  test("single sign-on: a work address goes through its provider and comes back signed in", async ({ page }) => {
    await page.route("**/identity/v1/sso/discover**", jsonRoute(200, { sso: true, workspace: "Acme", enforce: true }));
    let started = "";
    // the provider's round trip, short-circuited: back with a code for the workspace
    await page.route("**/identity/v1/sso/start**", async (route) => {
      started = route.request().url();
      const back = new URL(new URL(route.request().url()).searchParams.get("return_to")!, "http://localhost");
      await route.fulfill({ status: 302, headers: {
        location: `${back.pathname}?axor_login=code-1234567890abcdef&axor_org=org_1${back.hash}` } });
    });
    let exchanged: unknown = null;
    await page.route("**/identity/v1/oauth/exchange", async (route) => {
      exchanged = route.request().postDataJSON();
      await route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify({ ...SESSION, user: { user_id: "usr_9", email: "anna@acme.io" }, created: true }) });
    });
    // a password at an enforced domain is refused with the way to go
    await page.route("**/identity/v1/login", jsonRoute(403, { detail: {
      code: "sso_required", detail: "Acme signs in with single sign-on — use “Sign in with SSO”" } }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("anna@acme.io");
    await page.getByLabel("password").fill("whatever-it-is");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByText("Acme signs in with single sign-on — use “Sign in with SSO”")).toBeVisible();
    await expect(page.getByTestId("sso-offer")).toContainText("Acme signs in with single sign-on");
    await page.getByTestId("sso-continue").click();
    await expect(page.getByText("signed in as anna@acme.io")).toBeVisible();
    expect(started).toContain("email=anna%40acme.io");
    expect(exchanged).toEqual({ code: "code-1234567890abcdef", org_id: "org_1" });
  });

  test("provisioning: on Business an admin makes the provider's token, shown once", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "business" }],
    }));
    await page.route("**/identity/v1/orgs/org_1/members", jsonRoute(200, { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" }] }));
    await page.route("**/identity/v1/orgs/org_1/invites", jsonRoute(200, { invites: [] }));
    let enabled = false;
    await page.route("**/identity/v1/orgs/org_1/scim", async (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify({
        available: true, plan_ok: true, base_url: "https://cp.acme.io/identity/scim/v2", enabled,
        ...(enabled ? { created_ts: "2026-11-23T10:00:00Z", last_used_ts: null } : {}) }) }));
    await page.route("**/identity/v1/orgs/org_1/scim/token", async (route) => {
      enabled = true;
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({
        token: "scim_abcdef123456", base_url: "https://cp.acme.io/identity/scim/v2" }) });
    });
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    const scim = page.getByTestId("scim");
    await expect(scim.getByTestId("scim-base")).toHaveText("https://cp.acme.io/identity/scim/v2");
    await scim.getByTestId("scim-make").click();
    await expect(scim.getByTestId("scim-token")).toContainText("scim_abcdef123456");
    await expect(scim).toContainText("not used yet");
  });

  test("sign-ins: an admin sets how long they last and signs everyone else out", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "business" }],
    }));
    await page.route("**/identity/v1/orgs/org_1/members", jsonRoute(200, { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" }] }));
    await page.route("**/identity/v1/orgs/org_1/invites", jsonRoute(200, { invites: [] }));
    let hours: number | null = null;
    const policy = () => ({ hours, deployment_hours: 336, access_minutes: 15, plan_ok: true });
    await page.route("**/identity/v1/orgs/org_1/sessions", async (route) => {
      if (route.request().method() === "PUT") hours = JSON.parse(route.request().postData() ?? "{}").hours;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(policy()) });
    });
    await page.route("**/identity/v1/orgs/org_1/sessions/end",
      jsonRoute(200, { ended: 3, access_minutes: 15 }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    const box = page.getByTestId("sessions");
    await expect(box.getByTestId("session-hours")).toHaveValue("");
    await expect(box).toContainText("14 days (this deployment's)");
    await box.getByTestId("session-hours").selectOption("12");
    await expect(box.getByTestId("session-hours")).toHaveValue("12");
    expect(hours).toBe(12);
    await box.getByTestId("sessions-end").click();
    await answerAsk(page);
    await expect(box.getByTestId("sessions-ended")).toContainText("3 people signed out");
    await expect(box.getByTestId("sessions-ended")).toContainText("within 15 minutes");
  });

  test("SAML: the admin sees what to give the provider and pastes its metadata", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "business" }],
    }));
    await page.route("**/identity/v1/orgs/org_1/members", jsonRoute(200, { members: [] }));
    await page.route("**/identity/v1/orgs/org_1/invites", jsonRoute(200, { invites: [] }));
    const base = { available: true, plan_ok: true, callback_url: "https://cp.acme.io/identity/v1/sso/callback",
      provable_domains: ["acme.io"], saml_entity_id_ours: "https://cp.acme.io/identity/v1/saml/org_1",
      saml_acs_url: "https://cp.acme.io/identity/v1/saml/acs",
      saml_metadata_url: "https://cp.acme.io/identity/v1/saml/org_1/metadata",
      saml_cert_url: "https://cp.acme.io/identity/v1/saml/org_1/certificate" };
    let sent: Record<string, unknown> | null = null;
    await page.route("**/identity/v1/orgs/org_1/sso", async (route) => {
      if (route.request().method() === "PUT") sent = route.request().postDataJSON();
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(sent
        ? { ...base, configured: true, protocol: "saml", saml_entity_id: "https://idp.acme.io/saml",
            saml_sso_url: "https://idp.acme.io/sso", has_cert: true, domains: ["acme.io"],
            enforce: false, default_role: "member" }
        : { ...base, configured: false }) });
    });
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await page.getByTestId("sso-setup").click();
    await page.getByTestId("sso-protocol-saml").check();
    const ours = page.getByTestId("sso-saml-ours");
    await expect(ours).toContainText("https://cp.acme.io/identity/v1/saml/acs");
    await expect(ours).toContainText("https://cp.acme.io/identity/v1/saml/org_1");
    await expect(page.getByTestId("sso-saml-cert")).toHaveAttribute(
      "href", "https://cp.acme.io/identity/v1/saml/org_1/certificate");
    await page.getByTestId("sso-saml-metadata").fill("<md:EntityDescriptor entityID=\"https://idp.acme.io/saml\"/>");
    await page.getByTestId("sso-saml-sign").check();
    await page.getByTestId("sso-saml-idp-initiated").check();
    await page.getByTestId("sso-save").click();
    await expect(page.getByTestId("sso-on")).toContainText("SAML · https://idp.acme.io/saml");
    expect(sent).toMatchObject({ protocol: "saml", domains: ["acme.io"],
                                 saml_sign_requests: true, saml_idp_initiated: true,
                                 saml_metadata: "<md:EntityDescriptor entityID=\"https://idp.acme.io/saml\"/>" });
  });

  test("who may: an admin lets one member stop teammates' sessions", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }],
    }));
    await page.route("**/identity/v1/orgs/org_1/members", jsonRoute(200, { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" },
      { user_id: "usr_2", email: "bo@acme.io", role: "member", since: "2026-01-02" }] }));
    await page.route("**/identity/v1/orgs/org_1/invites", jsonRoute(200, { invites: [] }));
    const caps = {
      stop_others: { what: "pause, stop and cap a teammate's coding session or tree", members: false, people: [] as string[] },
      brake: { what: "hold every coding agent of the team at once (the team's brake)", members: false, people: [] as string[] },
      exceptions: { what: "let one refused thing through for the team, or trust a source", members: false, people: [] as string[] },
      skills: { what: "turn a skill off for the team, or share one with it", members: true, people: [] as string[] },
      memory: { what: "decide what goes into the team's memory", members: true, people: [] as string[] },
      production: { what: "pause, stop and steer production agents", members: true, people: [] as string[] },
    };
    const sent: unknown[] = [];
    await page.route("**/v1/permissions", async (route) => {
      if (route.request().method() === "PUT") {
        const body = route.request().postDataJSON() as Record<string, { people?: string[] }>;
        sent.push(body);
        if (body.stop_others?.people) caps.stop_others.people = body.stop_others.people;
      }
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        capabilities: caps, you: Object.fromEntries(Object.keys(caps).map((k) => [k, true])) }) });
    });
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    const row = page.getByTestId("may-stop_others");
    await expect(row).toContainText("pause, stop and cap a teammate's coding session");
    // only members can be named: the owner already may
    await expect(page.getByTestId("may-stop_others-add").locator("option")).toHaveText(["+ a person…", "bo@acme.io"]);
    await page.getByTestId("may-stop_others-add").selectOption("bo@acme.io");
    await expect(row).toContainText("bo@acme.io");
    expect(sent).toEqual([{ stop_others: { people: ["bo@acme.io"] } }]);
    await expect(page.getByTestId("may-skills-members")).toBeChecked();
  });

  test("a new tab is signed out, not signed in with nothing working (QA-004)", async ({ page, context }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();

    const other = await context.newPage();          // same browser, new tab
    await goHash(other, "settings");
    await expect(other.getByText("signed in as ada@acme.io")).toHaveCount(0);
    await expect(other.getByRole("button", { name: "ada@acme.io" })).toHaveCount(0);
    // and the first tab is untouched
    await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();
  });

  test("the header's account menu names the workspace and points to Settings for the rest", async ({ page }) => {
    // The whole administration (team, who may, sign-on, SCIM, keys) used to
    // render inside this 300px menu, which then ran off the bottom of the screen.
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }],
    }));
    await page.route("**/identity/v1/orgs/org_1/members", jsonRoute(200, { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" }] }));
    await page.route("**/identity/v1/orgs/org_1/invites", jsonRoute(200, { invites: [] }));
    await page.route("**/v1/permissions", jsonRoute(200, {
      capabilities: { skills: { what: "turn a skill off for the team, or share one with it",
                                members: true, people: [] } },
      you: { skills: true } }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByTestId("who-may")).toBeVisible();          // Settings: all of it

    await goHash(page, "govern");
    await page.getByRole("button", { name: "ada@acme.io" }).click();
    const menu = page.getByRole("dialog", { name: "account" });
    await expect(menu.getByText("signed in as ada@acme.io")).toBeVisible();
    await expect(menu.getByTestId("workspace-id")).toHaveText("(org_1)");
    await expect(menu.getByTestId("who-may")).toHaveCount(0);
    await menu.getByTestId("manage-workspace").click();
    await expect(menu).toHaveCount(0);
    await expect(page.getByTestId("who-may")).toBeVisible();
  });

  test("the account menu closes when the URL changes (QA-021)", async ({ page }) => {
    // in-app links closed it, but a URL typed or pasted (or back/forward) left
    // it open over the next page
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await goHash(page, "home");
    await page.getByRole("button", { name: "ada@acme.io" }).click();
    const menu = page.getByRole("dialog", { name: "account" });
    await expect(menu).toBeVisible();
    await page.evaluate(() => { window.location.hash = "#/settings?tab=billing"; });
    await expect(page).toHaveURL(/tab=billing/);
    await expect(menu).toHaveCount(0);
  });

  test("an admin changes a teammate's role, or takes them off — never the owner's", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }],
    }));
    let members = [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" },
      { user_id: "usr_2", email: "sam@acme.io", role: "member", since: "2026-02-01" }];
    await page.route("**/identity/v1/orgs/org_1/members", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ members }) }));
    const calls: string[] = [];
    await page.route("**/identity/v1/orgs/org_1/members/usr_2", async (route) => {
      const m = route.request().method();
      calls.push(m === "PUT" ? `PUT ${route.request().postDataJSON().role}` : m);
      members = m === "PUT"
        ? members.map((x) => x.user_id === "usr_2" ? { ...x, role: route.request().postDataJSON().role } : x)
        : members.filter((x) => x.user_id !== "usr_2");
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });
    await page.route("**/identity/v1/orgs/org_1/invites", jsonRoute(200, { invites: [] }));
    // taken off the workspace, their own keys on the plane go too
    await page.route("**/v1/keys/of/usr_2", async (route) => {
      calls.push(`keys ${route.request().method()}`);
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ revoked: ["k1"] }) });
    });
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    const team = page.getByTestId("team");
    await expect(team).toContainText("sam@acme.io");
    // the owner's own row is not a control
    await expect(page.getByLabel("role of ada@acme.io")).toHaveCount(0);
    await page.getByLabel("role of sam@acme.io").selectOption("admin");
    await expect(page.getByLabel("role of sam@acme.io")).toHaveValue("admin");
    await page.getByLabel("remove sam@acme.io").click();
    await answerAsk(page);
    await expect(team).not.toContainText("sam@acme.io");
    expect(calls).toEqual(["PUT admin", "DELETE", "keys DELETE"]);
  });

  test("an invitation link: a new account joins the workspace", async ({ page }) => {
    await page.route("**/v1/workspace", jsonRoute(200, { journey: "coding" }));
    await page.route("**/identity/v1/invites/tok-abc", jsonRoute(200, {
      org_name: "Acme", role: "member", for_email: null, expires_ts: "2026-12-01T00:00:00Z" }));
    let signed: unknown = null;
    await page.route("**/identity/v1/signup", async (route) => {
      signed = route.request().postDataJSON();
      await route.fulfill({ status: 201, contentType: "application/json", body: JSON.stringify({
        ...SESSION, user: { user_id: "usr_2", email: "sam@acme.io" },
        org: { org_id: "org_1", role: "member", tier: "coding" } }) });
    });
    await goHash(page, "invite/tok-abc");
    const inv = page.getByTestId("invite");
    await expect(inv).toContainText("Join Acme");
    await expect(inv).toContainText("invited as member");
    await inv.getByLabel("email").fill("sam@acme.io");
    await inv.getByLabel("password").fill("correct horse battery");
    await inv.getByTestId("invite-join").click();
    // straight to connecting their own machine, for what the team does
    await expect(page).toHaveURL(/#\/connect\?for=coding/);
    expect(signed).toMatchObject({ email: "sam@acme.io", invite: "tok-abc" });
  });

  test("an invite the plane refuses says why in words and keeps the page's width (QA-010)", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "business" }],
    }));
    await page.route("**/identity/v1/orgs/org_1/members", jsonRoute(200, { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" }] }));
    const long = "a".repeat(5000) + "@example.com";
    await page.route("**/identity/v1/orgs/org_1/invites", async (route) => {
      if (route.request().method() !== "POST") return route.fulfill({ json: { invites: [] } });
      // what the identity service answers: pydantic's 422, the input echoed back
      return route.fulfill({ status: 422, json: { detail: [{ type: "value_error", loc: ["body", "email"],
        msg: "value is not a valid email address: The email address is too long before the @-sign",
        input: long }] } });
    });
    await page.route("**/v1/license/entitlements", jsonRoute(200, {
      tier: "business", free: false, developers: 1, agent_families: 2, agent_family: null,
      locked: {}, locked_trees: {}, reasons: {} }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await page.getByLabel("invite role").selectOption("viewer");
    await page.getByLabel("invite email").fill(long);
    await page.getByTestId("invite-make").click();
    await expect(page.getByText("The email address is too long before the @-sign")).toBeVisible();
    await expect(page.getByText(/"loc"|"input"/)).toHaveCount(0);
    const width = await page.evaluate(() => document.documentElement.scrollWidth);
    expect(width).toBeLessThanOrEqual(page.viewportSize()!.width + 1);
  });

  test("on the free plan, inviting a second developer offers the paid plan", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }],
    }));
    await page.route("**/identity/v1/orgs/org_1/members", jsonRoute(200, { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" }] }));
    let invited = false;
    await page.route("**/identity/v1/orgs/org_1/invites", async (route) => {
      if (route.request().method() === "POST") invited = true;
      await route.fulfill({ status: route.request().method() === "POST" ? 201 : 200, contentType: "application/json",
        body: JSON.stringify(route.request().method() === "POST"
          ? { invite_id: "i", token: "tok-v", role: "viewer", email: null, expires_ts: "2026-12-01T00:00:00Z", mailed: false }
          : { invites: [] }) });
    });
    await page.route("**/v1/license/entitlements", jsonRoute(200, {
      tier: "community", free: true, developers: 1, agent_families: 1, agent_family: null,
      locked: {}, locked_trees: {}, reasons: {} }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await page.getByTestId("invite-make").click();
    await expect(page.getByTestId("invite-upgrade")).toContainText("A second developer is on the Coding plan");
    expect(invited).toBe(false);
    // a viewer watches, and is free
    // ...and the click invites them: one click, not a role switch and a second one
    await page.getByTestId("invite-as-viewer").click();
    await expect(page.getByTestId("invite-made")).toBeVisible();
    await expect(page.getByLabel("invite role")).toHaveValue("viewer");
    expect(invited).toBe(true);
  });

  test("on the free plan, making a viewer a member offers the plan first", async ({ page }) => {
    await page.route("**/identity/v1/signup-mode", jsonRoute(200, { mode: "open", signup: true }));
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }],
    }));
    await page.route("**/identity/v1/orgs/org_1/members", jsonRoute(200, { members: [
      { user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" },
      { user_id: "usr_2", email: "max@acme.io", role: "viewer", since: "2026-01-02" }] }));
    await page.route("**/identity/v1/orgs/org_1/invites", jsonRoute(200, { invites: [] }));
    let changed = false;
    await page.route("**/identity/v1/orgs/org_1/members/usr_2", async (route) => {
      changed = true;
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
    });
    await page.route("**/v1/license/entitlements", jsonRoute(200, {
      tier: "community", free: true, developers: 1, agent_families: 1, agent_family: null,
      locked: {}, locked_trees: {}, reasons: {} }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await page.getByLabel("role of max@acme.io").selectOption("member");
    await expect(page.getByTestId("promote-upgrade")).toContainText("second developer");
    expect(changed).toBe(false);
    await page.getByTestId("promote-keep").click();
    await expect(page.getByTestId("promote-upgrade")).toHaveCount(0);
    expect(changed).toBe(false);
  });

  test("alone in a workspace, pairing is not offered; with a teammate it is", async ({ page }) => {
    let members = [{ user_id: "usr_1", email: "ada@acme.io", role: "owner", since: "2026-01-01" }];
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "community" }],
    }));
    await page.route("**/identity/v1/orgs/org_1/members", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ members }) });
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await goHash(page, "govern");
    await expect(page.getByTestId("side-trees")).toBeVisible();
    await expect(page.getByTestId("side-pair")).toHaveCount(0);
    members = [...members, { user_id: "usr_2", email: "max@acme.io", role: "member", since: "2026-01-02" }];
    await page.reload();
    await expect(page.getByTestId("side-pair")).toBeVisible();
  });

  test("a used or withdrawn invitation says so", async ({ page }) => {
    await page.route("**/identity/v1/invites/tok-old", jsonRoute(404, {
      detail: "this invitation has expired, was used, or was withdrawn" }));
    await goHash(page, "invite/tok-old");
    await expect(page.getByTestId("invite-gone")).toContainText("was withdrawn");
  });

  test("logging out revokes the refresh token server-side", async ({ page }) => {
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: SESSION.user, active_org: "org_1", role: "member", memberships: [],
    }));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByRole("button", { name: "log out" })).toBeVisible();
    const revoked = page.waitForRequest("**/identity/v1/logout");
    await page.route("**/identity/v1/logout", (r) => r.fulfill({ status: 204 }));
    await page.getByRole("button", { name: "log out" }).click();
    expect((await revoked).postDataJSON()).toEqual({ refresh_token: "ref-token" });
  });
});

test.describe("sign in with GitHub / Google", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
  });

  test("a configured provider gets a button that starts there and comes back here", async ({ page }) => {
    await page.route("**/identity/v1/oauth/providers", jsonRoute(200, {
      providers: [{ name: "github", label: "GitHub" }],
    }));
    await goHash(page, "settings");
    const button = page.getByTestId("oauth-github");
    await expect(button).toHaveText("continue with GitHub");
    const href = await button.getAttribute("href");
    expect(href).toContain("/identity/v1/oauth/github/start?return_to=");
    expect(decodeURIComponent(href!.split("return_to=")[1])).toBe("/#/settings");
  });

  test("no provider set up, no button", async ({ page }) => {
    await page.route("**/identity/v1/oauth/providers", jsonRoute(200, { providers: [] }));
    await goHash(page, "settings");
    await expect(page.getByLabel("email")).toBeVisible();
    await expect(page.getByTestId("oauth-github")).toHaveCount(0);
  });

  test("the code a provider sends back becomes a session and leaves the address", async ({ page }) => {
    let traded = "";
    await page.route("**/identity/v1/oauth/exchange", async (route) => {
      traded = route.request().postDataJSON().code;
      await route.fulfill({ json: { ...SESSION, created: true } });
    });
    await page.goto("/?axor_login=one-time-code-123456#/settings");
    await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();
    expect(traded).toBe("one-time-code-123456");
    expect(page.url()).not.toContain("axor_login");
    expect(page.url()).toContain("#/settings");
  });

  test("a refused sign-in says why", async ({ page }) => {
    await page.goto("/?axor_login_error=GitHub%20sign-in%20was%20cancelled#/settings");
    await expect(page.getByTestId("auth-note")).toContainText("sign-in was cancelled");
    expect(page.url()).not.toContain("axor_login_error");
  });

  test("the reason in the address is never shown as written (QA-235)", async ({ page }) => {
    // anyone can write a link with any text in it: shown verbatim, it read as
    // the app's own banner
    await page.goto("/?axor_login_error=" + encodeURIComponent("Your account is locked. Call evil.example") + "#/settings");
    const note = page.getByTestId("auth-note");
    await expect(note).toContainText("sign-in did not complete — try again");
    await expect(note).not.toContainText("evil.example");
    // a provider's bare code is said in words too
    await page.goto("/?axor_login_error=access_denied#/settings");
    await expect(page.getByTestId("auth-note")).toContainText("sign-in was cancelled");
    await expect(page.getByTestId("auth-note")).not.toContainText("access_denied");
  });
});

test.describe("e-mail: reset and verification links", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
  });

  test("forgot password sends a link where mail is set up", async ({ page }) => {
    await page.route("**/identity/v1/email", jsonRoute(200, { available: true }));
    let asked: { email?: string; return_to?: string } = {};
    await page.route("**/identity/v1/password/forgot", async (route) => {
      asked = route.request().postDataJSON();
      await route.fulfill({ status: 202, json: { detail: "if that address has an account, a link is on its way" } });
    });
    await goHash(page, "settings");
    await page.getByRole("button", { name: "forgot password?" }).click();
    await expect(page.getByLabel("password")).toHaveCount(0);
    await page.getByLabel("email").fill("ada@acme.io");
    await page.getByRole("button", { name: "send reset link" }).click();
    await expect(page.getByTestId("reset-sent")).toContainText("a link is on its way");
    expect(asked).toEqual({ email: "ada@acme.io", return_to: "/#/settings" });
  });

  test("no mail set up, no forgot-password", async ({ page }) => {
    await page.route("**/identity/v1/email", jsonRoute(200, { available: false }));
    await goHash(page, "settings");
    await expect(page.getByLabel("email")).toBeVisible();
    await expect(page.getByRole("button", { name: "forgot password?" })).toHaveCount(0);
  });

  test("a reset link asks for a new password and sets it", async ({ page }) => {
    let body: { token?: string; password?: string } = {};
    await page.route("**/identity/v1/password/reset", async (route) => {
      body = route.request().postDataJSON();
      await route.fulfill({ status: 204, body: "" });
    });
    await page.goto("/?axor_reset=reset-token-123456789#/settings");
    await page.getByRole("textbox", { name: "new password" }).fill("brand new pass");
    await page.getByRole("button", { name: "set password" }).click();
    await expect(page.getByTestId("auth-note")).toContainText("password changed");
    expect(body).toEqual({ token: "reset-token-123456789", password: "brand new pass" });
    expect(page.url()).not.toContain("axor_reset");
  });

  test("a verification link confirms the address", async ({ page }) => {
    await page.route("**/identity/v1/email/verify", jsonRoute(200, { email: "ada@acme.io", email_verified: true }));
    await page.goto("/?axor_verify=verify-token-123456789#/settings");
    await expect(page.getByTestId("auth-note")).toContainText("ada@acme.io is confirmed");
  });

  test("signed in with an unconfirmed address offers to resend", async ({ page }) => {
    await page.route("**/identity/v1/email", jsonRoute(200, { available: true }));
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await page.route("**/identity/v1/me", jsonRoute(200, {
      user: { user_id: "usr_1", email: "ada@acme.io", email_verified: false },
      active_org: "org_1", role: "owner",
      memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "community" }],
    }));
    await page.route("**/identity/v1/email/verify/send", jsonRoute(202, { sent_to: "ada@acme.io" }));
    await goHash(page, "settings");
    await page.getByLabel("email").fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    const line = page.getByTestId("email-unverified");
    await line.getByRole("button", { name: "resend link" }).click();
    await expect(line).toContainText("link sent to ada@acme.io");
  });
});

test("alert mail is offered only where it can arrive: mail on, address confirmed", async ({ page }) => {
  await setConnection(page, { mode: "adapter" });
  await page.route("**/identity/v1/email", jsonRoute(200, { available: true }));
  await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
  await page.route("**/identity/v1/me", jsonRoute(200, {
    user: { user_id: "usr_1", email: "ada@acme.io", email_verified: false },
    active_org: "org_1", role: "owner",
    memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "community" }],
  }));
  await goHash(page, "settings");
  await page.getByLabel("email").fill("ada@acme.io");
  await page.getByLabel("password").fill("correct horse");
  await page.getByRole("button", { name: "log in", exact: true }).click();
  await page.getByTestId("settings-tab-alerts").click();
  // unconfirmed: the way to confirm, not a button that fails
  await expect(page.getByTestId("email-unverified")).toContainText("resend link");
  await expect(page.getByTestId("mail-me")).toHaveCount(0);
});

test("signed in, one click fills in drift alerts to your own address", async ({ page }) => {
  await setConnection(page, { mode: "adapter" });
  await page.route("**/identity/v1/email", jsonRoute(200, { available: true }));
  await page.route("**/identity/v1/me", jsonRoute(200, {
    user: { user_id: "usr_1", email: "ada@acme.io", email_verified: true },
    active_org: "org_1", role: "owner",
    memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "community" }],
  }));
  await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
  await goHash(page, "settings");
  await page.getByLabel("email").fill("ada@acme.io");
  await page.getByLabel("password").fill("correct horse");
  await page.getByRole("button", { name: "log in", exact: true }).click();
  await page.getByTestId("settings-tab-alerts").click();
  await page.getByTestId("mail-me").click();
  await expect(page.getByPlaceholder(/mailto:you/)).toHaveValue("mailto:ada@acme.io");
  await expect(page.getByLabel("an agent's behaviour drifted (its health check)")).toBeChecked();
});

test("a sign-up from the header says so and goes on to Connect (QA-036)", async ({ page }) => {
  // it closed the panel and left the new user on the marketing Home, nothing said
  await page.route("**/v1/auth/status", jsonRoute(200, { auth_enabled: true, authenticated: false, scopes: [] }));
  await page.route("**/identity/v1/signup-mode", jsonRoute(200, { mode: "open", signup: true }));
  await page.route("**/identity/v1/signup", jsonRoute(201, SESSION));
  await setConnection(page, { mode: "none" });
  await goHash(page, "home");
  await page.getByRole("button", { name: "sign in", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "sign in" });
  await dialog.getByRole("button", { name: "new? create a workspace" }).click();
  await dialog.getByLabel("email").fill("ada@acme.io");
  await dialog.getByLabel("password").fill("correct horse");
  await dialog.getByLabel("organization name").fill("Acme");
  await dialog.getByRole("button", { name: "create workspace" }).click();
  await expect(page.getByTestId("signup-done")).toContainText("your workspace is ready — signed in as ada@acme.io");
  await expect(page).toHaveURL(/#\/connect/);
});

test("the header's sign-in panel opens on log in, its buttons one line each (QA-022)", async ({ page }) => {
  // It opened on "create workspace" when the guest session had arrived before
  // the click and on "log in" when it had not; at 1366px "log in" wrapped.
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.route("**/v1/auth/status", jsonRoute(200, {
    auth_enabled: true, authenticated: false, scopes: [], guest_sessions: true, guest: false }));
  await page.route("**/v1/auth/guest", jsonRoute(201, {
    token: "gs_e2e_guest_token", org: "guest_e2e", ttl_seconds: 1800,
    expires_at: new Date(Date.now() + 29 * 60_000).toISOString() }));
  await page.route("**/identity/v1/signup-mode", jsonRoute(200, { mode: "open", signup: true }));
  await setConnection(page, { mode: "demo" });
  await goHash(page, "home");
  // the guest session is in: the case that used to open on "create workspace"
  await page.waitForFunction(() => (sessionStorage.getItem("axor-app") ?? "").includes("gs_e2e_guest_token"));
  await page.getByRole("button", { name: "sign in", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "sign in" });
  await expect(dialog.getByText("SIGN IN", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "log in", exact: true })).toBeVisible();
  const oneLine = async () => {
    for (const b of await dialog.locator("form button").all()) {
      expect((await b.boundingBox())!.height, await b.innerText()).toBeLessThan(32);
    }
  };
  await oneLine();
  await dialog.getByRole("button", { name: "new? create a workspace" }).click();
  await oneLine();
});

// QA-037: a day was cut from the UTC string ("until 2026-11-30"), so a viewer
// far from UTC saw the wrong day near midnight. The day is the viewer's own.
test.describe("days are the viewer's local day", () => {
  test.use({ timezoneId: "Pacific/Kiritimati" });   // +14: noon UTC is the next day

  test("an invitation says until which day, in local time", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await page.route("**/identity/v1/invites/tok-day", jsonRoute(200, {
      org_name: "Acme", role: "member", for_email: null, expires_ts: "2026-11-30T12:00:00Z" }));
    await goHash(page, "invite/tok-day");
    const inv = page.getByTestId("invite");
    await expect(inv).toContainText("until 2026-12-01.");
  });
});


// QA-150: the link identity e-mails is /?axor_invite=<token>; an anonymous
// visitor landed on Home, the token still in the address bar.
test.describe("an e-mailed invitation link", () => {
  for (const [form, url] of [["?axor_invite=", "/?axor_invite=tok-mail"], ["#/invite/", "/#/invite/tok-mail"]]) {
    test(`opens the invitation for anyone (${form})`, async ({ page }) => {
      await page.route("**/identity/v1/invites/tok-mail", jsonRoute(200, {
        org_name: "Acme", role: "member", for_email: null, expires_ts: "2026-12-01T00:00:00Z" }));
      await page.goto(url);
      await expect(page.getByTestId("invite")).toContainText("Join Acme");
      await expect(page).toHaveURL(/#\/invite\/tok-mail$/);
      expect(page.url()).not.toContain("axor_invite");
      // and it stays there: nothing routes it on to Home afterwards
      await page.waitForTimeout(1000);
      await expect(page.getByTestId("invite")).toBeVisible();
    });
  }
});

test.describe("invitations, signed in", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
    await goHash(page, "settings");
    await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
    await page.getByLabel("password").fill("correct horse");
    await page.getByRole("button", { name: "log in", exact: true }).click();
    await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();
  });

  test("one for another address says so up front, with no join button (QA-155)", async ({ page }) => {
    await page.route("**/identity/v1/invites/tok-sam", jsonRoute(200, {
      org_name: "Beta", role: "member", for_email: "sam@beta.io", expires_ts: "2026-12-01T00:00:00Z" }));
    await page.evaluate(() => { window.location.hash = "#/invite/tok-sam"; });
    await expect(page.getByTestId("invite-other-address"))
      .toContainText("This invitation is for another address, sam@beta.io");
    await expect(page.getByTestId("invite-join")).toHaveCount(0);
  });

  test("after joining, the used invitation is not fetched again (QA-157)", async ({ page }) => {
    await page.route("**/v1/workspace", jsonRoute(200, { journey: "coding" }));
    let reads = 0;
    await page.route("**/identity/v1/invites/tok-acc", (r) => {
      reads += 1;
      return reads === 1
        ? r.fulfill({ json: { org_name: "Beta", role: "member", for_email: "ada@acme.io",
                              expires_ts: "2026-12-01T00:00:00Z" } })
        : r.fulfill({ status: 404, json: { detail: "this invitation has expired, was used, or was withdrawn" } });
    });
    await page.route("**/identity/v1/invites/tok-acc/accept", jsonRoute(200, {
      ...SESSION, org: { org_id: "org_2", role: "member", tier: "community" } }));
    await page.evaluate(() => { window.location.hash = "#/invite/tok-acc"; });
    await page.getByTestId("invite-join").click();
    await expect(page).toHaveURL(/#\/connect/);
    await page.waitForTimeout(1000);
    expect(reads).toBe(1);
  });
});

// QA-154: a member taken off the team kept reading until the access token
// expired. The plane now refuses the token; the refresh says why, and so does
// the page.
test("a removed member is told in words that they are no longer in the workspace", async ({ page }) => {
  await setConnection(page, { mode: "adapter" });
  await page.route("**/identity/v1/login", jsonRoute(200, SESSION));
  await goHash(page, "settings");
  await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
  await page.getByLabel("password").fill("correct horse");
  await page.getByRole("button", { name: "log in", exact: true }).click();
  await expect(page.getByText("signed in as ada@acme.io")).toBeVisible();
  await page.route("**/identity/v1/refresh", jsonRoute(401, { detail: "you are no longer a member of Acme" }));
  await page.route(/\/v1\/(runs|plane|incidents|workspace)/, (r) =>
    r.request().headers()["authorization"] === "Bearer acc-token"
      ? r.fulfill({ status: 401, json: { error: "unauthorized",
                                         detail: "you are no longer a member of this workspace" } })
      : r.fallback());
  await goHash(page, "evidence");
  await expect(page.getByTestId("session-ended")).toContainText("you are no longer a member of Acme");
});
