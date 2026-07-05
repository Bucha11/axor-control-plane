// Backend client. Vite dev-proxies /v1 -> backend :8400 and /axor -> proxy :8401.

export interface KernelEvent {
  schema_version: string;
  seq: number;
  node_id: string;
  kind: string;
  ts: string;
  causal_root: string | null;
  gate: string | null;
  verdict: "pass" | "deny" | null;
  payload: Record<string, unknown>;
}

export interface RunSummary {
  run_id: string;
  node_id: string;
  scenario: string;
  intervened: boolean;
  completed: boolean;
  evidence: EvidenceCaseDto[];
  created_ts: string;
}

export interface EvidenceCaseDto {
  scenario: string;
  deviation: string | null;
  verdict_source: string;
  confidence: number;
  observed_reality: unknown;
  agent_claim: unknown;
  fault_attribution: { fault_mode: string; tool_name: string; influence: string }[];
}

export interface ScrubberStep {
  seq: number;
  kind: string;
  gate: string | null;
  recorded_verdict: string | null;
  reevaluated_verdict: string | null;
  deny_reason: string | null;
  deny_category: string | null;
  hypothetical: boolean;
  payload: Record<string, unknown>;
  state: {
    level: string;
    tainted_refs: string[];
    excised_refs: string[];
    floor_active: boolean;
    budget_spent_calls: number;
    facts: number;
  };
}

export interface ScrubberPayload {
  first_divergence: number | null;
  steps: ScrubberStep[];
}

export interface NodeInfo {
  node_id: string;
  desired: { version: number; state: Record<string, unknown> } | null;
  reported: {
    applied_version: number;
    level: string;
    budget_remaining: number | null;
    updated_ts: string;
  } | null;
  facts: Record<string, unknown>[];
}

export interface RegressionRow {
  run_id: string;
  side: string;
  label: string;
  result: "held" | "escaped" | "passed" | "regressed";
  first_divergence: number | null;
  new_denial: { seq: number; reason: string; category: string } | null;
}

export interface RegressionReport {
  rows: RegressionRow[];
  regressed: number;
  escaped: number;
  safe_to_ship: boolean;
}

async function j<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json() as Promise<T>;
}

export const api = {
  listRuns: () => fetch("/v1/runs").then((r) => j<RunSummary[]>(r)),
  runEvents: (runId: string) =>
    fetch(`/v1/runs/${runId}/events`).then((r) => j<KernelEvent[]>(r)),
  scrubber: (runId: string) =>
    fetch(`/v1/replay/${runId}`).then((r) => j<ScrubberPayload>(r)),
  counterfactual: (runId: string, config: Record<string, unknown>) =>
    fetch(`/v1/replay/${runId}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ config }),
    }).then((r) => j<ScrubberPayload>(r)),
  nodes: () => fetch("/v1/plane/nodes").then((r) => j<NodeInfo[]>(r)),
  command: (nodeId: string, version: number, state: Record<string, unknown>) =>
    fetch(`/v1/plane/${nodeId}/command`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        version,
        state,
        operator: "op_ui",
        timestamp: new Date().toISOString(),
        sig: "",
      }),
    }).then((r) => j<{ node_id: string; version: number; state: Record<string, unknown> }>(r)),
  pin: (runId: string, side: "must_block" | "must_pass", label: string) =>
    fetch(`/v1/pins/${runId}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ side, label }),
    }).then((r) => j<{ pinned: string }>(r)),
  regression: (config: Record<string, unknown>) =>
    fetch("/v1/regression", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ config }),
    }).then((r) => j<RegressionReport>(r)),
  proxyPreflight: () =>
    fetch("/axor/preflight").then((r) =>
      j<{ all_ok: boolean; tools: Record<string, { ok: boolean; status?: number; error?: string }> }>(r),
    ),
};
