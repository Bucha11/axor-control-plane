// Chaos tests — every run with a tool broken on purpose, what the agent said,
// arming one on a live agent's next run, and the schedule (Team).
import { expect, test } from "@playwright/test";
import { BACKEND, goHash, setConnection, uniqueNode } from "./helpers";

const line = (node: string, seq: number, kind: string, payload: object) => ({
  schema_version: "1.0", seq, node_id: node, kind, ts: new Date().toISOString(),
  causal_root: null, gate: null, verdict: null, payload,
});

test("a broken tool, what the agent said, and arming one on a live agent", async ({ page, request }) => {
  const node = uniqueNode("chaos");
  const r = await request.post(`${BACKEND}/v1/plane/${node}/telemetry`, { data: {
    run_id: `${node}-r1`, events: [
      line(node, 0, "tool_call", { tool: "send_email", args: { to: "a@b.c" } }),
      line(node, 1, "fault_injected", { tool: "send_email", mode: "silent_fail" }),
      line(node, 2, "tool_result", { tool: "send_email", status: "error" }),
      line(node, 3, "claim", { text: "I could not send it: the email tool failed.", structured: false }),
    ] } });
  expect(r.ok()).toBeTruthy();
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "chaos");
  await expect(page.getByRole("button", { name: "Chaos tests", exact: true })).toHaveAttribute("aria-current", "page");
  const row = page.getByTestId("chaos-row").filter({ hasText: node });
  await expect(row).toContainText("send_email — returning an error");
  await expect(row).toContainText("no lie found");
  await expect(row).toContainText("the email tool failed");

  const live = page.getByTestId("chaos-live");
  await live.getByRole("button", { name: node }).click();
  await live.getByLabel("how to break it").selectOption("corrupt_retrieval");
  // a double-click arms once (the QA-045/087/101 sweep: a create button
  // took the second click of a double-click before it turned disabled)
  let arms = 0;
  await page.route("**/v1/chaos/arm", async (r) => { arms += 1; await new Promise((ok) => setTimeout(ok, 300)); await r.continue(); });
  await live.getByTestId("chaos-arm").dblclick();
  await expect(page.getByTestId("chaos-armed")).toContainText(`armed: ${node} — send_email, poisoning its results`);
  expect(arms).toBe(1);

  // QA-500: still armed after a reload, until the agent answers — then what it said
  await page.reload();
  await expect(page.getByTestId("chaos-armed")).toContainText(`armed: ${node} — send_email, poisoning its results`);
  const desired = await (await request.get(`${BACKEND}/v1/plane/nodes`)).json();
  const id = desired.find((n: { node_id: string }) => n.node_id === node).desired.state.pending_experiment.id;
  expect((await request.post(`${BACKEND}/v1/plane/${node}/telemetry`, { data: { run_id: node, events: [
    line(node, 10, "operator_intervention", { action: "experiment_refused", id, reason: "not test-bench" }),
  ] } })).ok()).toBeTruthy();
  expect((await request.post(`${BACKEND}/v1/plane/${node}/consumed`, { data: { key: "pending_experiment" } })).ok()).toBeTruthy();
  await page.reload();
  await expect(page.getByTestId("chaos-armed")).toContainText(`not taken: ${node} — not test-bench`);
  await expect(page.getByTestId("chaos-armed")).not.toContainText(`armed: ${node}`);

  // the schedule is Team: shown, locked, with the way to it
  await expect(page.getByTestId("chaos-schedule")).toContainText("part of Team");
});
