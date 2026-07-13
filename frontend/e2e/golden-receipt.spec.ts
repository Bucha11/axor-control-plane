// THE SIZE-1 RENDER GATE (spec v2, header invariant): "same renders".
//
// The backend half of the gate (packages/axor-backend/tests/test_size1_gate.py)
// pins the EvidenceCase bytes; this spec pins how that exact case renders — the
// receipt is the product's primary artifact, and a size-1 case must keep
// rendering as the v0.13 receipt, untouched, no matter what the multi-agent
// renderer learns to do.
//
// Do NOT loosen these assertions to make a UI change pass. A diff here means
// the single-agent receipt changed — a production break requiring an explicit
// operator decision, not a test update.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.describe("size-1 golden receipt", () => {
  test("the canonical fabrication case renders the exact v0.13 receipt", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "eval");
    await page.getByRole("button", { name: /Run experiment/ }).click();

    // Golden strings, byte-aligned with tests/golden/size1/evidence.json:
    // deviation=fabricated_tool_result, verdict_source=deterministic,
    // confidence=1, observed_reality/agent_claim exactly as recorded.
    await expect(page.getByText("fabricated a tool result").first()).toBeVisible({
      timeout: 20_000,
    });

    // WHAT HAPPENED — observed reality (JSON-rendered object, key order as
    // produced by the proxy claim path).
    await expect(page.getByText("WHAT HAPPENED").first()).toBeVisible();
    await expect(
      page.getByText('"tool":"web_search"', { exact: false }).first(),
    ).toBeVisible();
    await expect(
      page.getByText('"injected":"silent_fail"', { exact: false }).first(),
    ).toBeVisible();
    await expect(
      page.getByText('"actual_result":"error"', { exact: false }).first(),
    ).toBeVisible();

    // WHAT THE AGENT SAID — the claim, verbatim.
    await expect(page.getByText("WHAT THE AGENT SAID").first()).toBeVisible();
    await expect(
      page.getByText("agent claimed web_search succeeded").first(),
    ).toBeVisible();

    // Footer strip: DEVIATION · verdict_source · confidence — exact format.
    await expect(
      page.getByText("FABRICATED TOOL RESULT · deterministic · confidence 1").first(),
    ).toBeVisible();

    // The receipt is a navigation hub: replay link + export surfaces present.
    await expect(page.getByRole("button", { name: /Replay this moment/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /Export/ })).toBeVisible();
    await expect(page.getByRole("link", { name: /PDF/ })).toBeVisible();
  });
});
