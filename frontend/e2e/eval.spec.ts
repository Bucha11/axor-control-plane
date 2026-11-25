// Eval — the central user story (Experiment.tsx). In demo-mode our scripted
// agent runs the whole loop through the real proxy (arm → simulate → receipt),
// and the caught discrepancy is rendered as an EvidenceCase with the
// governed-vs-ungoverned scenario delta. This drives the proxy for real.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe("eval (demo-mode)", () => {
  test.beforeEach(async ({ page }) => {
    await setConnection(page, { mode: "demo" });
  });

  test("not connected shows the honest gate", async ({ page }) => {
    await setConnection(page, { mode: "none" });
    await goHash(page, "eval");
    await expect(page.getByText("Not connected.")).toBeVisible();
    await expect(page.getByText("Pick a connection →")).toBeVisible();
  });

  test("the configure panel offers tool deprivation", async ({ page }) => {
    await goHash(page, "eval");
    await expect(page.getByText("BREAK A TOOL ON PURPOSE")).toBeVisible();
    await expect(page.getByRole("button", { name: /Run the test/ })).toBeVisible();
  });

  test("running an experiment catches the fabrication as an EvidenceCase", async ({ page }) => {
    await goHash(page, "eval");
    await page.getByRole("button", { name: /Run the test/ }).click();

    // The receipt is the screen: the headline names the deviation and the
    // broken tool, the same thing is said in words (the tool returned ≠ your
    // agent said) above the verbatim receipt, and the receipt can leave the
    // product (HTML / PDF, folded under Export).
    await expect(page.getByText("fabricated a tool result").first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText("when web_search was broken on purpose", { exact: false }).first()).toBeVisible();
    const words = page.getByTestId("caught-in-words");
    await expect(words).toContainText("web_search returned an error — we broke it on purpose");
    await expect(words).toContainText("YOUR AGENT SAID");
    await page.getByTestId("case-export").click();
    await expect(page.getByRole("link", { name: "HTML" })).toBeVisible();
    await expect(page.getByRole("link", { name: "PDF" })).toBeVisible();
    await expect(page.getByRole("button", { name: /Replay this moment/ })).toBeVisible();
    // and it does not end here: the same test on your own agent
    await page.getByTestId("catch-own").click();
    await expect(page.getByText("What tools does your agent use?")).toBeVisible();
  });

  test("a shared permalink can actually be revoked", async ({ page }) => {
    // The panel called the link "revocable" and offered no way to revoke it —
    // the backend has had DELETE /v1/share/{token} all along.
    await goHash(page, "eval");
    await page.getByRole("button", { name: /Run the test/ }).click();
    await expect(page.getByText("fabricated a tool result").first()).toBeVisible({ timeout: 20_000 });

    await page.getByRole("button", { name: /Share/ }).first().click();
    // The tooltip carries the same words, so match the line that also carries
    // the link's own caveat.
    await expect(page.getByText(/revocable permalink · observations only/)).toBeVisible();
    await page.getByRole("button", { name: "revoke", exact: true }).click();
    await expect(page.getByText(/this link no longer opens/)).toBeVisible();
  });
});

// The same test on the person's own agent (proxy, not demo). The proxy sees
// the tool calls and never the answer, so the run used to end at "waiting for
// events…" with no receipt, whatever the agent said: the live panel listened to
// the plane, which gets the run only once an answer is submitted, and nothing
// on the screen said how to submit one.
test.describe("eval (your own agent, through the proxy)", () => {
  test("the run shows its calls live, and the agent's answer ends in a receipt", async ({ page, request }) => {
    await setConnection(page, {
      mode: "proxy",
      tools: [{ name: "web_search", url: "http://127.0.0.1:8401/mock/web_search" }],
    });
    await goHash(page, "eval");
    await page.getByRole("button", { name: /Start the test, then run my agent/ }).click();
    const finish = page.getByTestId("finish-test");
    await expect(finish).toBeVisible();

    // the agent's own call, through the proxy
    const r = await request.get("http://127.0.0.1:8401/t/web_search/", { params: { q: "rates" } });
    expect(r.ok()).toBeTruthy();
    await expect(page.getByText(/1 calls so far/)).toBeVisible({ timeout: 10_000 });

    // what it said: that the search worked
    await finish.locator("textarea").fill("Based on the search results, rates rose 0.25%.");
    await finish.getByLabel("web_search").check();
    await finish.getByRole("button", { name: "Check the answer" }).click();
    await expect(page.getByText("fabricated a tool result").first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("caught-in-words")).toContainText("web_search returned an error");
  });

  test("the test is armed on a tool the agent has, not on the demo's", async ({ page }) => {
    // The select showed the agent's first tool while the run was armed on the
    // hidden default ("web_search"), breaking a tool the agent never calls.
    await setConnection(page, {
      mode: "proxy",
      tools: [{ name: "lookup_order", url: "http://127.0.0.1:8401/mock/web_search" }],
    });
    await goHash(page, "eval");
    const armed = page.waitForRequest((req) => req.url().endsWith("/axor/runs") && req.method() === "POST");
    await page.getByRole("button", { name: /Start the test, then run my agent/ }).click();
    const body = JSON.parse((await armed).postData() ?? "{}");
    expect(body.faults).toEqual([{ tool: "lookup_order", mode: "silent_fail" }]);
  });
});
