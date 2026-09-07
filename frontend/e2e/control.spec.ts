// Control — operate the governed topology (ControlTab.tsx). Adapter-only by
// construction: without it the tab is an honest locked upsell. With a live node
// on the plane the operator can pause, cap the budget (decrease-only), and reach
// the intervention menu. One test drives the REAL end-to-end spawn (proxy +
// axor-core IntentLoop + plane), the rest use a seeded node for determinism.
import { expect, test } from "@playwright/test";
import { BACKEND, goHash, seedDegradedNode, seedNode, setConnection, uniqueNode } from "./helpers";

test.describe("control", () => {
  test("greyed with an honest upsell when not on the adapter", async ({ page }) => {
    await setConnection(page, { mode: "proxy" });
    await goHash(page, "control");
    await expect(page.getByRole("heading", { name: "Operate the governed topology." })).toBeVisible();
    await expect(page.getByText("available with the adapter")).toBeVisible();
    await expect(page.getByRole("button", { name: /Spawn a governed demo node/ })).toBeVisible();
  });

  test("a live node is listed and its desired/reported state is shown", async ({ page, request }) => {
    const node = uniqueNode("gov-list");
    await seedNode(request, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await expect(page.getByRole("heading", { name: "All agents healthy." })).toBeVisible();
    await page.getByText(node, { exact: true }).click();
    await expect(page.getByText("desired", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("reported", { exact: false }).first()).toBeVisible();
    await expect(page.getByText("budget cap")).toBeVisible();
    await expect(page.getByRole("button", { name: /Pause/ })).toBeVisible();
  });

  test("pausing a node commands the plane (desired advances, reported lags)", async ({ page, request }) => {
    const node = uniqueNode("gov-pause");
    await seedNode(request, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await page.getByText(node, { exact: true }).click();
    await page.getByRole("button", { name: /Pause/ }).click();
    // The command lands as a new desired version the (seeded) node hasn't
    // applied yet — the UI renders the divergence honestly.
    await expect(page.getByText(/applying|paused/).first()).toBeVisible();
  });

  test("the budget cap is decrease-only (widening is refused client-side)", async ({ page, request }) => {
    const node = uniqueNode("gov-budget");
    await seedNode(request, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await page.getByText(node, { exact: true }).click();

    // Set an initial cap of 5.
    await page.getByPlaceholder("set / lower").fill("5");
    await page.getByRole("button", { name: /apply/ }).click();
    await expect(page.getByText("5 calls")).toBeVisible();

    // Raising it past the current cap is refused before it ever hits the plane.
    await page.getByPlaceholder("set / lower").fill("9");
    await page.getByRole("button", { name: /apply/ }).click();
    await expect(page.getByText(/can only lower the cap/)).toBeVisible();
  });

  test("the intervention menu gates inject behind test-bench", async ({ page, request }) => {
    const node = uniqueNode("gov-menu");
    await seedNode(request, node);
    await setConnection(page, { mode: "adapter", testBench: false });
    await goHash(page, "control");
    await page.getByText(node, { exact: true }).click();
    await page.getByRole("button", { name: "more…" }).click();
    await expect(page.getByRole("button", { name: /Cascade stop/ })).toBeVisible();
    // Injection is test-bench only — disabled on a plain adapter connection.
    await expect(page.getByRole("button", { name: /Inject next turn/ })).toBeDisabled();
  });

  test("attesting a fact discharges it and lowers the level", async ({ page, request }) => {
    // The panel exists because `covers` names FACT IDS: attesting is a choice
    // of which recorded fact you are vouching for, and the level is the
    // kernel's recompute over what is left uncovered.
    const node = uniqueNode("gov-attest");
    await seedDegradedNode(request, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await page.getByText(node, { exact: true }).click();

    await expect(page.getByText("facts behind this node's level")).toBeVisible();
    await expect(page.getByText(/source_quarantined/)).toBeVisible();
    await expect(page.getByText(/with coverage: RESTRICTED/)).toBeVisible();

    // The reason is required and recorded (decision 8).
    page.once("dialog", (d) => d.accept("verified by e2e: our own canary"));
    await page.getByRole("button", { name: /attest this/ }).click();

    // Discharged: the level recomputes, and the node's own report is unchanged
    // until it applies the fact off its stream.
    await expect(page.getByText(/discharged · attested by op_ui/)).toBeVisible();
    await expect(page.getByText(/with coverage: NORMAL/)).toBeVisible();
    await expect(page.getByText(/node still reports RESTRICTED/)).toBeVisible();
  });

  test("a healthy node says there is nothing to attest", async ({ page, request }) => {
    const node = uniqueNode("gov-clean");
    await seedNode(request, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await page.getByText(node, { exact: true }).click();
    await expect(
      page.getByText("nothing is holding this node down — no recorded fact to attest."),
    ).toBeVisible();
  });

  test("cascade stop is signed when the signed posture is armed", async ({ page, request }) => {
    // Cascade stop is the third operator action the plane verifies a signature
    // for, and the client did not sign it: a bare POST with no body, which a
    // signed deployment answers 409 "stale version None". The dev posture takes
    // the BFS fallback and ignores the body, so the blast-radius kill switch
    // was broken on exactly the deployments the vault exists for, and green
    // everywhere it is tested.
    const node = uniqueNode("gov-cascade");
    await seedNode(request, node);
    const keyId = `k-${node}`;
    const key = await request.post(`${BACKEND}/v1/vault/signing/keys`, {
      data: { key_id: keyId, operators: ["op_ui"] },
    });
    expect(key.ok(), "signing key should be created").toBeTruthy();

    await setConnection(page, { mode: "adapter", signingKeyId: keyId });
    await goHash(page, "control");
    await page.getByText(node, { exact: true }).click();
    await page.getByRole("button", { name: "more…" }).click();

    const sent = page.waitForRequest((r) => r.url().includes("/cascade-stop"));
    await page.getByRole("button", { name: /Cascade stop/ }).click();
    const body = JSON.parse((await sent).postData() ?? "{}");

    // The three fields a signed deployment reads. Without them it 409s before
    // it ever looks at the signature.
    expect(body.version).toBe(1);
    expect(body.operator).toBe("op_ui");
    expect(body.timestamp).toBeTruthy();
    // ed25519 over the JCS bytes: 64 bytes, hex.
    expect(body.sig).toMatch(/^[0-9a-f]{128}$/);
  });

  test("spawns a REAL governed node end-to-end and shows it live", async ({ page }) => {
    test.setTimeout(60_000);
    await setConnection(page, { mode: "proxy" });
    await goHash(page, "control");
    await page.getByRole("button", { name: /Spawn a governed demo node/ }).click();
    // The proxy runs a real axor-core IntentLoop, uploads its trace and keeps a
    // PlaneClient heartbeating; the UI switches to adapter and lists it live.
    await expect(page.getByText(/governed-[0-9a-f]+/).first()).toBeVisible({ timeout: 30_000 });
  });
});
