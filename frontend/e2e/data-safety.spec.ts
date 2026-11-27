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
