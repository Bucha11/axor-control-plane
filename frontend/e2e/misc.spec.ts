// The remaining secondary surfaces: pricing (the monetization lines) and health
// (probe baseline). Light coverage — these are mostly static, but they must
// render and stay on-message.
import { expect, request, test } from "@playwright/test";
import { BACKEND, goHash, setConnection } from "./helpers";

test.describe("secondary surfaces", () => {
  test("pricing states the two monetization lines", async ({ page }) => {
    await goHash(page, "pricing");
    await expect(page.getByRole("heading", { name: "Pricing" })).toBeVisible();
    await expect(
      page.getByText("anything that makes an agent safer is free forever", { exact: false }),
    ).toBeVisible();
    await expect(
      page.getByText("you pay for capabilities, organizational maturity and fleet size", {
        exact: false,
      }),
    ).toBeVisible();
  });

  test("health reports the probe baseline", async ({ page }) => {
    // A node that never posted a battery honestly reads "no health check yet",
    // so seed one CONSISTENT check the way the node would: out-dial POST.
    const ctx = await request.newContext();
    await ctx.post(`${BACKEND}/v1/plane/banking-assistant/probe-report`, {
      data: {
        session_id: "sess-e2e",
        agent_id: "banking-assistant",
        model: "demo",
        probe_library_version: "1.0.0",
        overall_verdict: "CONSISTENT",
        families: [
          { family: "data_disclosure", state: "clean", escapes: 0, probes: 1 },
          { family: "scope_expansion", state: "clean", escapes: 0, probes: 1 },
          { family: "identity_probe", state: "clean", escapes: 0, probes: 1 },
        ],
        probes_sent: 3,
        probes_invalid: 0,
        probes_triangulated: 0,
        structural_failures: 0,
        escape_count: 0,
        escape_rate: 0,
        escape_rate_ci: [0, 0.56],
        calibration_status: "UNCALIBRATED",
        max_drift_score_uncalibrated: 0.1,
      },
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "health");
    await expect(
      page.getByText(/Agent behavior is on baseline\.|A probe family drifted from baseline\./),
    ).toBeVisible();
  });

  // Self-heal used to append an operator_attestation fact and then say it was
  // awaiting a verifying re-probe — a record that somebody intervened, next to
  // a context nothing had touched. The assertion that matters is therefore not
  // about the panel: it is that the node's DESIRED STATE carries the excision
  // afterwards, because that is the only thing the adapter ever reads.
  test("self-heal cuts the fragments the localizer blamed", async ({ page }) => {
    const ctx = await request.newContext();
    await ctx.post(`${BACKEND}/v1/plane/banking-assistant/probe-report`, {
      data: {
        session_id: "sess-drift", agent_id: "banking-assistant", model: "demo",
        probe_library_version: "1.0.0", overall_verdict: "DRIFT_DETECTED",
        families: [
          { family: "data_disclosure", state: "escaped", escapes: 2, probes: 4 },
          { family: "scope_expansion", state: "clean", escapes: 0, probes: 4 },
        ],
        probes_sent: 8, probes_invalid: 0, probes_triangulated: 0,
        structural_failures: 0, escape_count: 2, escape_rate: 0.25,
        escape_rate_ci: [0.07, 0.59], calibration_status: "UNCALIBRATED",
        max_drift_score_uncalibrated: 0.7,
        // what the node's localizer found: one pure-tainted fragment causes it
        repair_proposal: {
          verdict: "auto_excise",
          drift_fragments: ["frag_web_3"], excision: ["frag_web_3"],
          auto_excise: ["frag_web_3"], escalate: [],
          recommend_quarantine_all: false, approximate: false,
        },
      },
    });
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "health");

    await expect(page.getByRole("heading", { name: /A probe family drifted/ })).toBeVisible();
    await expect(page.getByText("Self-heal excises 1 context fragment", { exact: false })).toBeVisible();
    await page.getByRole("button", { name: /Self-heal/ }).click();
    await page.getByPlaceholder("reason (required)", { exact: false })
      .fill("drift after the retrieval change");
    await page.getByRole("button", { name: /excise & re-probe/ }).click();

    // the panel is honest about what is outstanding
    await expect(page.getByText("awaiting the verifying re-probe", { exact: false }))
      .toBeVisible();

    // and the node was actually told to cut it
    const desired = await (await ctx.get(
      `${BACKEND}/v1/plane/banking-assistant/repair`,
    )).json();
    expect(desired.pending.target_refs).toEqual(["frag_web_3"]);
    expect(desired.history[0].reason).toBe("drift after the retrieval change");
    expect(desired.history[0].reprobe_verdict).toBeNull();

    // the verifying re-probe closes the pair, and only then does it read healed
    await ctx.post(`${BACKEND}/v1/plane/banking-assistant/probe-report`, {
      data: {
        session_id: "sess-verify", agent_id: "banking-assistant", model: "demo",
        probe_library_version: "1.0.0", overall_verdict: "CONSISTENT",
        families: [
          { family: "data_disclosure", state: "clean", escapes: 0, probes: 4 },
          { family: "scope_expansion", state: "clean", escapes: 0, probes: 4 },
        ],
        probes_sent: 8, probes_invalid: 0, probes_triangulated: 0,
        structural_failures: 0, escape_count: 0, escape_rate: 0,
        escape_rate_ci: [0, 0.37], calibration_status: "UNCALIBRATED",
        max_drift_score_uncalibrated: 0.1,
      },
    });
    await page.getByRole("button", { name: /Refresh/ }).click();
    await expect(page.getByText("re-probe: OK", { exact: false })).toBeVisible();
  });
});

// The demo landing: the single-agent story stays the hero (operator
// decision); the two-tree containment story is the second screen.
test.describe("demo landing", () => {
  test("single-agent hero leads; two-tree containment is the second screen", async ({ page }) => {
    await page.goto("/demo.html");
    // hero: the single-agent story
    await expect(page.getByRole("heading", { level: 1, name: /Your agent lies when its tools fail/ })).toBeVisible();
    // second screen: the multi-agent two-tree story, both worlds
    await expect(page.getByRole("heading", { level: 2, name: /Watch the lie spread/ })).toBeVisible();
    await expect(page.getByText("UNGOVERNED")).toBeVisible();
    await expect(page.getByText("GOVERNED", { exact: true })).toBeVisible();

    // the two-tree recording plays to the divergence + containment table
    await page.getByRole("button", { name: /Run the recording/ }).nth(1).click();
    await expect(page.getByText("FABRICATION ESCAPED")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("CONTAINED", { exact: true })).toBeVisible();
    await expect(page.getByText("1/1 boundaries held")).toBeVisible();
    await expect(page.getByText("fabricated_failure")).toBeVisible();
  });
});
