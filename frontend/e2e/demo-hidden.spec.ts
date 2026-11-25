// Once the workspace's own agents report (work_started), the product's demo is
// out of the way on every screen — not only the example agents the backend
// already hides from its lists, but every offer to open one. The suite's plane
// keeps its examples (AXOR_KEEP_EXAMPLES), so the moment is played by answering
// /v1/workspace as a workspace whose agents have reported.
import { expect, test, type Page } from "@playwright/test";
import { goHash, resetPolicy } from "./helpers";

async function ownAgentsReported(page: Page): Promise<void> {
  await page.route("**/v1/workspace", (route) =>
    route.fulfill({ json: { journey: null, work_started: true } }));
}

test.describe("the demo once your own agents report", () => {
  test("before: the example is offered (the control for what follows)", async ({ page }) => {
    await goHash(page, "chaos");
    await expect(page.getByTestId("chaos-example")).toBeVisible();
  });

  test("Home, Chaos tests and Safe to ship offer no example", async ({ page }) => {
    await ownAgentsReported(page);
    // a corpus of the person's own: the example's rules are not offered
    await page.route("**/v1/pins", (route) =>
      route.request().method() === "GET" ? route.fulfill({ json: { pins: [] } }) : route.fallback());

    await goHash(page, "home?for=production");
    await expect(page.getByTestId("home-connect-production")).toBeVisible();
    await expect(page.getByText("watch it happen")).toHaveCount(0);
    await expect(page.getByTestId("home-example-fleet")).toHaveCount(0);

    await goHash(page, "chaos");
    await expect(page.getByText("on my agent →")).toBeVisible();
    await expect(page.getByTestId("chaos-example")).toHaveCount(0);
    await expect(page.getByText("try it on the example")).toHaveCount(0);

    await goHash(page, "prevent");
    await expect(page.getByTestId("rules-policy")).toBeVisible();
    await expect(page.getByTestId("rules-example")).toHaveCount(0);
  });

  test("a test run on your agent offers no example or mock tools", async ({ page, request }) => {
    await resetPolicy(request);
    await ownAgentsReported(page);
    await goHash(page, "get-started");
    await expect(page.getByRole("heading", { name: "What tools does your agent use?" })).toBeVisible();
    await expect(page.getByRole("button", { name: /Load example tools/ })).toHaveCount(0);
    await expect(page.getByText("use our mock tools")).toHaveCount(0);
  });

  test("the proxy as a guard offers no example tools", async ({ page, request }) => {
    await resetPolicy(request);
    await ownAgentsReported(page);
    await goHash(page, "connect?for=production");
    await page.getByTestId("prod-way-proxy").click();
    await expect(page.getByRole("heading", { name: "A proxy that guards your agent's tools" })).toBeVisible();
    await expect(page.getByTestId("guard-example")).toHaveCount(0);
  });

  test("context health, team memory and skills offer no example", async ({ page }) => {
    await ownAgentsReported(page);
    await goHash(page, "health");
    await expect(page.getByTestId("setup-health-example")).toHaveCount(0);
    await expect(page.getByText("made-up numbers")).toHaveCount(0);
    await goHash(page, "memory");
    await expect(page.getByTestId("memory-example")).toHaveCount(0);
    await goHash(page, "skills");
    await expect(page.getByTestId("skill-example")).toHaveCount(0);
    await expect(page.getByTestId("skill-try-example")).toHaveCount(0);
  });
});
