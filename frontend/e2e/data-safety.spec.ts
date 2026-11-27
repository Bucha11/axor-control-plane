// Never lose or overwrite the workspace's data, and say when a read failed:
// a save from a screen that read an older policy is refused with what changed
// (QA-212); a failed read is never shown as "nothing yet" (QA-231, 233, 234).
import { expect, test, type Browser, type Page } from "@playwright/test";
import { BACKEND, goHash, setConnection } from "./helpers";

async function journey(page: Page, j: string) {
  await page.addInitScript((value) => {
    const raw = localStorage.getItem("axor-app");
    const data = raw ? JSON.parse(raw) : { state: {}, version: 0 };
    data.state.journey = value;
    data.state.menuAll = false;
    localStorage.setItem("axor-app", JSON.stringify(data));
  }, j);
}

async function codingPolicyTab(browser: Browser): Promise<Page> {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await setConnection(page, { mode: "adapter" });
  await journey(page, "coding");
  await goHash(page, "policy");
  await expect(page.getByTestId("rule-secrets")).toBeVisible();
  return page;
}

test.describe("two tabs on one policy (QA-212)", () => {
  test("the tab that read before is refused, told what changed, and not told saved", async ({ browser, request }) => {
    const clear = { allow: [], mode: "enforce", exceptions: [], trusted_sources: ["docs.python.org"] };
    await request.put(`${BACKEND}/v1/coding/rules`, { data: clear });
    const a = await codingPolicyTab(browser);
    const b = await codingPolicyTab(browser);
    // A: an exception, and the docs source removed
    const exA = a.getByTestId("policy-exceptions");
    await exA.getByLabel("rule").selectOption("secrets");
    await exA.getByTestId("policy-exception-value").fill("tests/a/*");
    await exA.getByTestId("policy-exception-add").click();
    await expect(exA.getByTestId("policy-exception")).toContainText("tests/a/*");
    await a.getByLabel("stop trusting docs.python.org").click();
    await expect(a.getByTestId("trusted-source")).toHaveCount(0);
    // B, still on what it read: an exception, a source, a rule toggle
    const exB = b.getByTestId("policy-exceptions");
    await exB.getByLabel("rule").selectOption("secrets");
    await exB.getByTestId("policy-exception-value").fill("tests/b/*");
    await exB.getByTestId("policy-exception-add").click();
    await expect(exB.getByTestId("policy-stale")).toContainText(/someone changed the exceptions and the trusted sources since you opened this — reload to see it/);
    const srcB = b.getByTestId("policy-trusted-sources");
    await srcB.getByTestId("trusted-source-host").fill("pypi.org");
    await srcB.getByTestId("trusted-source-add").click();
    await expect(srcB.getByTestId("policy-stale")).toBeVisible();
    await b.getByTestId("rule-secrets-allowed").click();
    await b.getByTestId("policy-save").click();
    await expect(b.getByTestId("coding-policy").getByTestId("policy-stale").first()).toBeVisible();
    await expect(b.getByText(/^saved/)).toHaveCount(0);
    // the plane holds A's policy, whole
    const held = await (await request.get(`${BACKEND}/v1/coding/rules`)).json();
    expect(held.exceptions.map((e: { path: string }) => e.path)).toEqual(["tests/a/*"]);
    expect(held.trusted_sources).toEqual([]);
    expect(held.allow).toEqual([]);
    // B reloads: sees A's, and its next save goes through
    await exB.getByTestId("policy-reload").click();
    await expect(exB.getByTestId("policy-exception")).toContainText("tests/a/*");
    await expect(exB.getByTestId("policy-stale")).toHaveCount(0);
    await srcB.getByTestId("trusted-source-host").fill("pypi.org");
    await srcB.getByTestId("trusted-source-add").click();
    await expect(srcB.getByTestId("trusted-source")).toHaveText(/pypi\.org/);
    await a.context().close();
    await b.context().close();
    await request.put(`${BACKEND}/v1/coding/rules`, { data: { ...clear, trusted_sources: [] } });
  });

  test("the tool policy: a save over a change made elsewhere is refused and said on Policy", async ({ page, request }) => {
    await request.put(`${BACKEND}/v1/policy`, { data: { tools: {} } });
    await setConnection(page, { mode: "adapter" });
    await journey(page, "production");
    await goHash(page, "connect?for=production");
    await page.getByTestId("prod-way-proxy").click();
    await page.getByTestId("proxy-mode-test").click();
    const name = page.getByPlaceholder("tool name ⏎");
    const url = page.getByPlaceholder("https://endpoint ⏎");
    await name.fill("first_tool");
    await url.fill("https://first.example/api");
    await url.press("Enter");
    await expect.poll(async () => Object.keys((await (await request.get(`${BACKEND}/v1/policy`)).json()).tools))
      .toContain("first_tool");
    // someone else saves the policy (another tab, another person)
    const theirs = await (await request.get(`${BACKEND}/v1/policy`)).json();
    await request.put(`${BACKEND}/v1/policy`, { data: { version: theirs.version, tools: {
      ...theirs.tools, first_tool: { ...theirs.tools.first_tool, klass: "EXPORT" } } } });
    await name.fill("second_tool");
    await url.fill("https://second.example/api");
    await url.press("Enter");
    await page.evaluate(() => { window.location.hash = "#/policy"; });
    await expect(page.getByTestId("policy-stale")).toContainText(/someone changed the policy since you opened it \(first_tool\) — reload to see it/);
    const held = await (await request.get(`${BACKEND}/v1/policy`)).json();
    expect(held.tools.first_tool.klass).toBe("EXPORT");
    expect(Object.keys(held.tools)).not.toContain("second_tool");
    await page.getByTestId("policy-reload").click();
    await expect(page.getByTestId("policy-stale")).toHaveCount(0);
    await request.put(`${BACKEND}/v1/policy`, { data: { tools: {} } });
  });
});

test.describe("a failed read is said, never shown as empty (QA-231, QA-233, QA-234)", () => {
  const fail = (route: import("@playwright/test").Route) =>
    route.fulfill({ status: 500, contentType: "text/plain", body: "Internal Server Error" });

  test("QA-231: the coding policy unread offers no save and no 'no team policy yet'", async ({ page }) => {
    await page.route("**/v1/coding/rules", fail);
    await setConnection(page, { mode: "adapter" });
    await journey(page, "coding");
    await goHash(page, "policy");
    await expect(page.getByTestId("read-failed")).toContainText("could not load the team's policy");
    await expect(page.getByTestId("read-failed")).not.toContainText("Internal Server Error");
    await expect(page.getByTestId("policy-not-set")).toHaveCount(0);
    await expect(page.getByTestId("policy-save")).toHaveCount(0);
    await page.unroute("**/v1/coding/rules");
    await page.getByTestId("read-failed-retry").click();
    await expect(page.getByTestId("rule-secrets")).toBeVisible();
    await expect(page.getByTestId("read-failed")).toHaveCount(0);
  });

  test("QA-231: the tool policy unread is said on Policy, and nothing is saved over it", async ({ page, request }) => {
    await request.put(`${BACKEND}/v1/policy`, { data: { tools: { kept_tool: { klass: "EXPORT", outside: false } } } });
    const puts: string[] = [];
    page.on("request", (r) => { if (r.method() === "PUT" && r.url().endsWith("/v1/policy")) puts.push(r.url()); });
    await page.route("**/v1/policy", (route) => route.request().method() === "GET" ? fail(route) : route.continue());
    await setConnection(page, { mode: "adapter" });
    await journey(page, "production");
    await goHash(page, "connect?for=production");
    await page.getByTestId("prod-way-proxy").click();
    await page.getByTestId("proxy-mode-test").click();
    await page.getByPlaceholder("tool name ⏎").fill("local_tool");
    await page.getByPlaceholder("https://endpoint ⏎").fill("https://local.example/api");
    await page.getByPlaceholder("https://endpoint ⏎").press("Enter");
    await page.waitForTimeout(1500);   // the save would have gone by now
    expect(puts).toEqual([]);
    await page.evaluate(() => { window.location.hash = "#/policy"; });
    await expect(page.getByTestId("policy-read-failed")).toContainText("could not read the workspace's policy");
    await expect(page.getByTestId("policy-read-failed")).not.toContainText("Internal Server Error");
    const held = await (await request.get(`${BACKEND}/v1/policy`)).json();
    expect(Object.keys(held.tools)).toEqual(["kept_tool"]);
    await page.unroute("**/v1/policy");
    await page.getByTestId("policy-reload").click();
    await expect(page.getByTestId("policy-read-failed")).toHaveCount(0);
    await request.put(`${BACKEND}/v1/policy`, { data: { tools: {} } });
  });

  test("QA-233: a failed fleet read says so with a retry, not 'no agents yet'", async ({ page }) => {
    await page.route("**/v1/plane/fleet", fail);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "govern");
    await expect(page.getByTestId("read-failed")).toContainText("could not load the agents");
    await expect(page.getByText("No agents on the plane yet")).toHaveCount(0);
    await expect(page.getByText("Load an example fleet")).toHaveCount(0);
    await page.unroute("**/v1/plane/fleet");
    await page.getByTestId("read-failed-retry").click();
    await expect(page.getByTestId("read-failed")).toHaveCount(0);
  });

  test("QA-233: a failed read of the agents themselves says so too", async ({ page }) => {
    await page.route("**/v1/plane/nodes", fail);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "govern");
    await expect(page.getByTestId("read-failed")).toContainText("could not load the agents", { timeout: 15_000 });
    await expect(page.getByText("No agents on the plane yet")).toHaveCount(0);
  });

  test("QA-234: a failed incidents read is said in words, not 'Internal Server Error'", async ({ page }) => {
    await page.route("**/v1/incidents**", fail);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "evidence");
    await expect(page.getByTestId("read-failed")).toContainText(
      "could not load the incidents — the plane could not answer (HTTP 500)", { timeout: 15_000 });
    await expect(page.getByText("Internal Server Error")).toHaveCount(0);
    await page.unroute("**/v1/incidents**");
    await page.getByTestId("read-failed-retry").click();
    await expect(page.getByTestId("read-failed")).toHaveCount(0);
  });
});
