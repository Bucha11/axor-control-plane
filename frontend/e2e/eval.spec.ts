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
