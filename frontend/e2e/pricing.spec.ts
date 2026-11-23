// Pricing, Both ways in: how many developers run coding agents, how many
// production agents — the cheapest plan and why, from the list prices
// (src/quote.ts, the same numbers as the backend's ee/pricing.py).
import { expect, test } from "@playwright/test";
import { goHash } from "./helpers";

test("both ways in: the calculator names the cheapest plan and what the next one adds", async ({ page }) => {
  await goHash(page, "pricing?for=both");
  const calc = page.getByTestId("pricing-calculator");
  const set = async (devs: number, agents: number) => {
    await calc.getByTestId("calc-devs").fill(String(devs));
    await calc.getByTestId("calc-agents").fill(String(agents));
  };
  // a coding team with one production agent: no jump to Team for it
  await set(20, 1);
  await expect(calc.getByTestId("calc-best")).toContainText("Coding — $500 / month");
  // the next plan up, and what it is for
  await expect(calc.getByTestId("calc-next")).toContainText("Business is $300 more ($800) and adds single sign-on");
  // ten production agents: Team is cheaper now
  await set(20, 10);
  await expect(calc.getByTestId("calc-best")).toContainText("Team Workspace — $899 / month");
  await expect(calc.getByTestId("calc-lines")).toContainText("15 developers × $40");
  await expect(calc.getByTestId("calc-all")).toContainText("Coding: $1,175");
  // one developer, one agent: free
  await set(1, 1);
  await expect(calc.getByTestId("calc-best")).toContainText("Community — $0 / month");
  // production only
  await set(0, 12);
  await expect(calc.getByTestId("calc-best")).toContainText("Team Workspace — $449 / month");
  await expect(calc.getByTestId("calc-all")).toContainText("Community: — the free plan governs one production agent");
  // the Coding card is on this way in too, and says what it takes
  await expect(page.getByTestId("tier-coding")).toContainText("one included, then $75 per agent");
  await expect(page.getByTestId("tier-business")).toContainText("$40 / developer / mo");
});

test("only one way in shows no calculator", async ({ page }) => {
  await goHash(page, "pricing?for=coding");
  await expect(page.getByTestId("tier-coding")).toBeVisible();
  await expect(page.getByTestId("pricing-calculator")).toHaveCount(0);
});
