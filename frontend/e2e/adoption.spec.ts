// Adoption layer: hover/focus tooltips on the non-obvious actions, and an
// opt-in Learn mode that reveals per-surface coach notes. Default stays
// quiet-until-wrong — no coach notes until the user asks for them.
import { expect, test } from "@playwright/test";
import { goHash, seedNode, setConnection, uniqueNode } from "./helpers";

test.describe("adoption", () => {
  test("a button reveals a themed tooltip on hover", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "eval");
    await expect(page.getByRole("tooltip")).toHaveCount(0);
    await page.getByRole("button", { name: /Run experiment/ }).hover();
    await expect(page.getByRole("tooltip")).toContainText("arm the scenario", { ignoreCase: true });
  });

  test("Learn mode is off by default — no coach notes", async ({ page }) => {
    await setConnection(page, { mode: "demo" });
    await goHash(page, "eval");
    await expect(page.getByRole("note")).toHaveCount(0);
  });

  test("the first-visit nudge turns Learn mode on and reveals coach notes", async ({ page }) => {
    // Connected (so the deep surfaces render) but learn not yet seen, so the
    // Home nudge is still offered. setConnection must run before the first load.
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "home");
    await expect(page.getByText("New here?", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: "Turn on", exact: true }).click();

    // Now every primary surface carries its coach note (hash nav keeps the
    // in-memory store, so learn stays on).
    await goHash(page, "eval");
    await expect(page.getByRole("note")).toContainText("EvidenceCase");
    await goHash(page, "replay");
    await expect(page.getByRole("note")).toContainText("counterfactual");
    await goHash(page, "regression");
    await expect(page.getByRole("note")).toContainText("safe to ship", { ignoreCase: true });
  });

  test("every secondary surface carries its coach note in Learn mode", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await page.addInitScript(() => {
      const raw = localStorage.getItem("axor-app");
      const data = raw ? JSON.parse(raw) : { state: {}, version: 0 };
      data.state.learnMode = true;
      data.state.learnSeen = true;
      data.state.coachDismissed = [];
      localStorage.setItem("axor-app", JSON.stringify(data));
    });
    const cases: [string, string | RegExp][] = [
      ["config-builder", "consequence class"],
      ["get-started", "byte-for-byte"],
      ["health", "re-anchors"],
      ["settings", "dead-letter"],
      ["expert", "one screen"],
      ["pricing", "free forever"],
    ];
    for (const [hash, expected] of cases) {
      await goHash(page, hash);
      await expect(page.getByRole("note").first()).toContainText(expected as string, { ignoreCase: true });
    }
  });

  test("reset tips brings back dismissed coach notes", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "settings");
    // Turn learn on, dismiss the settings note, then reset it from the section.
    await page.getByRole("button", { name: /toggle learn mode/ }).click();
    await expect(page.getByRole("note")).toBeVisible();
    await page.getByRole("note").getByRole("button", { name: /dismiss tip/ }).click();
    await expect(page.getByRole("note")).toHaveCount(0);
    await page.getByRole("button", { name: /reset tips/ }).click();
    await expect(page.getByRole("note")).toBeVisible();
  });

  test("the header toggle controls Learn mode and a note can be dismissed", async ({ page, request }) => {
    const node = uniqueNode("gov-coach");
    await seedNode(request, node);
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "control");

    // Off by default.
    await expect(page.getByRole("note")).toHaveCount(0);
    // Toggle on from the header.
    await page.getByRole("button", { name: /toggle learn mode/ }).click();
    await expect(page.getByRole("note")).toContainText("live governed agent", { ignoreCase: true });
    // Dismiss just this note.
    await page.getByRole("note").getByRole("button", { name: /dismiss tip/ }).click();
    await expect(page.getByRole("note")).toHaveCount(0);
  });
});
