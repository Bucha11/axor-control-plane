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
});
