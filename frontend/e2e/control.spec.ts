// Control — operate the governed topology (ControlTab.tsx). Adapter-only by
// construction: without it the tab is an honest locked upsell. With a live node
// on the plane the operator can pause, cap the budget (decrease-only), and reach
// the intervention menu. One test drives the REAL end-to-end spawn (proxy +
// axor-core IntentLoop + plane), the rest use a seeded node for determinism.
import { expect, test } from "@playwright/test";
import { goHash, seedNode, setConnection, uniqueNode } from "./helpers";

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
    await expect(page.getByRole("button", { name: /Attest branch/ })).toBeVisible();
    // Injection is test-bench only — disabled on a plain adapter connection.
    await expect(page.getByRole("button", { name: /Inject next turn/ })).toBeDisabled();
  });

  test("attesting a branch records an operator fact", async ({ page, request }) => {
    const node = uniqueNode("gov-attest");
    await seedNode(request, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");
    await page.getByText(node, { exact: true }).click();
    await page.getByRole("button", { name: "more…" }).click();
    // Attest uses a prompt for the required reason (decision 8).
    page.once("dialog", (d) => d.accept("verified by e2e"));
    await page.getByRole("button", { name: /Attest branch/ }).click();
    // No error surfaces (a missing reason would show one).
    await expect(page.getByText("attestation requires a reason")).toHaveCount(0);
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
