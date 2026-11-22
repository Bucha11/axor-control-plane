// The axor-identity sign-in panel in Settings. The e2e stack runs the backend
// and proxy but not the identity service, so /identity/* is stubbed per test —
// this exercises the panel's UI behavior (sign in, error, sign up, log out),
// not the identity service itself (that has its own suite).
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

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
    page.once("dialog", (d) => void d.accept());
    await page.getByLabel("remove sam@acme.io").click();
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
    await expect(page.getByTestId("auth-note")).toContainText("GitHub sign-in was cancelled");
    expect(page.url()).not.toContain("axor_login_error");
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
