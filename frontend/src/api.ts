import { useApp } from "./store";

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
    budget_spent_cost?: number;
    facts: number;
  };
}

export interface GraphEdge {
  src: string;
  dst: string;
  run_id: string;
}

export interface GraphKhop {
  focus: string;
  nodes: string[];
  edges: GraphEdge[];
}

export interface BranchAttestation {
  fact_id: string;
  operator: string;
  reason: string;
  revokes: string | null;
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

export interface SimulateResult {
  run_id: string;
  evidence: EvidenceCaseDto[];
  deviations: number;
  upload?: { uploaded: boolean; events?: number; evidence?: number } | null;
}

export interface StartRunResult {
  run_id: string;
  armed: boolean;
  tools: Record<string, string>;
}

export interface DeadLetter {
  url: string;
  error: string;
  attempts: number;
  trigger: string;
}

export interface LicenseInfo {
  org: string;
  tier: string;
  node_ceiling: number;
  expiry: string;
  features: string[];
}

async function j<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json() as Promise<T>;
}

// The backend API token (local token or API key) is read from the store at call
// time and sent as the bearer. When auth is off it is empty and omitted.
function apiToken(): string {
  return useApp.getState().apiToken;
}

// Authed fetch: merges the Authorization header into any request.
function af(path: string, init: RequestInit = {}): Promise<Response> {
  const token = apiToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(path, { ...init, headers });
}

// Append the token as a query param for URLs the browser opens directly (SSE
// via EventSource, export links in <a>) — those cannot carry a header.
function withToken(url: string): string {
  const token = apiToken();
  if (!token) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
}

export const api = {
  listRuns: () => af("/v1/runs").then((r) => j<RunSummary[]>(r)),
  runEvents: (runId: string) =>
    af(`/v1/runs/${runId}/events`).then((r) => j<KernelEvent[]>(r)),
  scrubber: (runId: string) =>
    af(`/v1/replay/${runId}`).then((r) => j<ScrubberPayload>(r)),
  counterfactual: (runId: string, config: Record<string, unknown>) =>
    af(`/v1/replay/${runId}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ config }),
    }).then((r) => j<ScrubberPayload>(r)),
  nodes: () => af("/v1/plane/nodes").then((r) => j<NodeInfo[]>(r)),
  command: (nodeId: string, version: number, state: Record<string, unknown>) =>
    af(`/v1/plane/${nodeId}/command`, {
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
    af(`/v1/pins/${runId}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ side, label }),
    }).then((r) => j<{ pinned: string }>(r)),
  regression: (config: Record<string, unknown>) =>
    af("/v1/regression", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ config }),
    }).then((r) => j<RegressionReport>(r)),
  proxyPreflight: () =>
    af("/axor/preflight").then((r) =>
      j<{ all_ok: boolean; tools: Record<string, { ok: boolean; status?: number; error?: string }> }>(r),
    ),
  proxyHealth: () =>
    af("/axor/healthz").then((r) => j<{ ok: boolean; armed: boolean }>(r)),

  // ── experiment loop (proxy) ────────────────────────────────────────────────
  startRun: (scenario: string, faults: { tool: string; mode: string }[], nodeId = "proxy") =>
    af("/axor/runs", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ scenario, faults, node_id: nodeId }),
    }).then((r) => j<StartRunResult>(r)),
  simulate: (runId: string, body: Record<string, unknown> = {}) =>
    af(`/axor/runs/${runId}/simulate`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => j<SimulateResult>(r)),

  // ── EvidenceCase share / export (spec 8.3) ─────────────────────────────────
  shareCase: (runId: string, caseIndex: number) =>
    af(`/v1/runs/${runId}/cases/${caseIndex}/share`, { method: "POST" }).then(
      (r) => j<{ token: string; url: string }>(r),
    ),
  revokeShare: (token: string) =>
    af(`/v1/share/${token}`, { method: "DELETE" }).then((r) => j<{ revoked: string }>(r)),
  exportUrl: (runId: string, caseIndex: number) =>
    withToken(`/v1/runs/${runId}/cases/${caseIndex}/export`),

  // ── notifications (spec 16) ────────────────────────────────────────────────
  subscribeNotifications: (url: string, triggers: string[], debounceSeconds = 0) =>
    af("/v1/notifications/subscribe", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url, triggers, debounce_seconds: debounceSeconds }),
    }).then((r) => j<{ subscribed: string; triggers: string[] }>(r)),
  deadLetters: () =>
    af("/v1/notifications/dead-letters").then((r) => j<DeadLetter[]>(r)),

  // ── operator interventions over the plane (spec §12) ───────────────────────
  appendFact: (nodeId: string, fact: Record<string, unknown>) =>
    af(`/v1/plane/${nodeId}/facts`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        fact, operator: "op_ui", timestamp: new Date().toISOString(), sig: "",
      }),
    }).then((r) => j<{ appended: boolean }>(r)),
  cascadeStop: (nodeId: string) =>
    af(`/v1/plane/${nodeId}/cascade-stop`, { method: "POST" }).then(
      (r) => j<{ stopped: string[]; count: number }>(r),
    ),

  // ── taint / provenance graph (spec decision 6) ─────────────────────────────
  graphKhop: (focus: string, k = 2, limit = 100) =>
    af(`/v1/graph/khop?focus=${encodeURIComponent(focus)}&k=${k}&limit=${limit}`).then(
      (r) => j<GraphKhop>(r),
    ),
  graphAttestations: (ref: string) =>
    af(`/v1/graph/attestations?ref=${encodeURIComponent(ref)}`).then(
      (r) => j<BranchAttestation[]>(r),
    ),

  // ── EE license (monetization 4) ────────────────────────────────────────────
  verifyLicense: (licenseJson: string, vendorPubkey: string) =>
    af("/v1/license/verify", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ license_json: licenseJson, vendor_pubkey: vendorPubkey }),
    }).then((r) => j<LicenseInfo>(r)),

  // ── auth: local token + scoped API keys (architecture section 9) ───────────
  authStatus: () =>
    af("/v1/auth/status").then((r) =>
      j<{ auth_enabled: boolean; authenticated: boolean; scopes: string[] }>(r),
    ),
  listKeys: () =>
    af("/v1/keys").then((r) =>
      j<{ key_id: string; scopes: string[]; label: string; created_ts: string }[]>(r),
    ),
  createKey: (scopes: string[], label: string) =>
    af("/v1/keys", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ scopes, label }),
    }).then((r) => j<{ key_id: string; secret: string; scopes: string[] }>(r)),
  revokeKey: (keyId: string) =>
    af(`/v1/keys/${keyId}`, { method: "DELETE" }).then((r) => j<{ revoked: string }>(r)),
};

// Live audit stream (spec 8): SSE of colour-coded events for a run. Returns an
// unsubscribe fn. Uses fetch-based EventSource polyfill semantics via the
// browser EventSource (backend serves text/event-stream at /v1/runs/{id}/stream).
export function streamRun(
  runId: string,
  onEvent: (event: KernelEvent) => void,
): () => void {
  const source = new EventSource(withToken(`/v1/runs/${runId}/stream`));
  source.addEventListener("event", (e) => {
    try {
      onEvent(JSON.parse((e as MessageEvent).data) as KernelEvent);
    } catch {
      /* ignore malformed frame */
    }
  });
  return () => source.close();
}
