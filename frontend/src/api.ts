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

export interface CaseAnchor {
  node_id: string;
  seq: number;
}

export interface SubgraphNode {
  node_id: string;
  roles: string[];
  seqs: number[];
}

export interface SubgraphEdge {
  from: string;
  to: string;
  kind: string;
  carried: { root?: { sources: string[]; sensitive: boolean } };
  gate_verdict: string | null;
  msg_id: string | null;
}

export interface SubgraphPayload {
  anchor: CaseAnchor;
  nodes: SubgraphNode[];
  edges: SubgraphEdge[];
  fault_origin: CaseAnchor | null;
  contained_at: { from: string; to: string; kind: string; gate: string | null }[] | null;
  federation_scope: "intra" | "inter";
}

export interface ContainmentReport {
  rows: { edge: string; note: string; status: "carried" | "held" | "escaped"; gate?: string | null }[];
  held: number;
  reached: number;
  containment: string | null;
  governed_outcome: string;
  ungoverned_outcome: string;
}

export interface InfluenceEntry {
  ref: string;
  influence: number;
  baseline_verdict: string | null;
  ablated_verdict: string | null;
}

export interface EvidenceCaseDto {
  scenario: string;
  deviation: string | null;
  verdict_source: string;
  confidence: number;
  observed_reality: unknown;
  agent_claim: unknown;
  fault_attribution: { fault_mode: string; tool_name: string; influence: string }[];
  // Multi-agent (spec v2 Ch.3): present => the case has a causal subgraph to
  // derive on open. Absent on every size-1 case — the v0.13 render is used.
  anchor?: CaseAnchor | null;
  twin_ref?: { trace_id: string } | null;
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

// Topology (spec v2 Ch.4 §6): derived from traced spawn/message events only.
export interface TopologyNode {
  node_id: string;
  kind: "self" | "peer";
  desired?: { version: number; state: Record<string, unknown> } | null;
  reported?: NodeInfo["reported"];
}

export interface TopologyEdge {
  from: string;
  to: string;
  kind: "delegation" | "lateral" | "peer";
  messages: number;
  denied: number;
  last_gate: string | null;
  spawned?: boolean;
}

export interface TopologyPayload {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
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
  created_ts?: string; // persisted (survives restarts) since migration 0002
}

export interface LicenseInfo {
  org: string;
  tier: string;
  node_ceiling: number;
  expiry: string;
  features: string[];
  live_nodes?: number;
  over_ceiling?: boolean;
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

  topology: () => af("/v1/plane/topology").then((r) => j<TopologyPayload>(r)),

  subgraph: (runId: string, anchor: CaseAnchor) =>
    af(`/v1/runs/${runId}/subgraph?anchor_node=${encodeURIComponent(anchor.node_id)}&anchor_seq=${anchor.seq}`)
      .then((r) => j<SubgraphPayload>(r)),

  containment: (runId: string, anchor: CaseAnchor) =>
    af(`/v1/runs/${runId}/containment?anchor_node=${encodeURIComponent(anchor.node_id)}&anchor_seq=${anchor.seq}`)
      .then((r) => j<ContainmentReport>(r)),

  influence: (runId: string, anchor: CaseAnchor, config: Record<string, unknown>) =>
    af(`/v1/runs/${runId}/influence`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ anchor_node: anchor.node_id, anchor_seq: anchor.seq, config }),
    }).then((r) => j<{ ranking: InfluenceEntry[] }>(r)),

  vaultCredsHealth: () =>
    af("/v1/vault/creds/health").then((r) =>
      j<{ enrolled: { tool: string; endpoint: string; version: number; revoked: boolean; scope_nodes: string[] }[] }>(r)),

  vaultSigningKeys: () =>
    af("/v1/vault/signing/keys").then((r) =>
      j<{ key_id: string; public_key_hex: string; operators: string[]; created_ts: string }[]>(r)),

  vaultSigningAudit: () =>
    af("/v1/vault/signing/audit").then((r) =>
      j<{ operator: string; key_id: string; payload_sha256: string; granted: boolean; ts: string }[]>(r)),

  spawnGovernedTree: () =>
    af("/axor/governed/spawn-tree", { method: "POST" }).then((r) =>
      j<{ run_id: string; nodes: Record<string, string>; denials: number; events: number }>(r)),

  seedTreeRun: () =>
    af("/v1/demo/seed-tree-run", { method: "POST" }).then((r) => j<unknown>(r)),
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
  // ── MCP onboarding: discover an MCP server's tools + register it. Either an
  // HTTP url or a local stdio command (the proxy spawns it as a gateway). ────
  mcpDiscover: (target: { url?: string; command?: string[] }, name?: string) =>
    af("/axor/mcp/discover", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...target, ...(name ? { name } : {}) }),
    }).then((r) =>
      j<{ registered: string; proxied_base: string; server: string;
          protocol_version: string; transport: "http" | "stdio";
          tools: { name: string; description: string }[] }>(r),
    ),
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
  exportUrl: (runId: string, caseIndex: number, format: "html" | "pdf" = "html") =>
    withToken(`/v1/runs/${runId}/cases/${caseIndex}/export?format=${format}`),

  // ── notifications (spec 16) ────────────────────────────────────────────────
  subscribeNotifications: (
    url: string, triggers: string[], debounceSeconds = 0,
    routing?: { label?: string; nodePattern?: string },
  ) =>
    af("/v1/notifications/subscribe", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        url, triggers, debounce_seconds: debounceSeconds,
        ...(routing?.label ? { label: routing.label } : {}),
        ...(routing?.nodePattern ? { node_pattern: routing.nodePattern } : {}),
      }),
    }).then((r) => j<{ subscribed: string; triggers: string[] }>(r)),
  listSubscriptions: () =>
    af("/v1/notifications/subscriptions").then((r) =>
      j<{ url: string; triggers: string[]; debounce_seconds: number;
          label: string; node_pattern: string }[]>(r),
    ),
  deadLetters: () =>
    af("/v1/notifications/dead-letters").then((r) => j<DeadLetter[]>(r)),

  // ── org features (EE): scheduled corpus CI + history ───────────────────────
  regressionHistory: (limit = 50) =>
    af(`/v1/regression/history?limit=${limit}`).then((r) =>
      j<{ created_ts: string; source: string; regressed: number; escaped: number;
          skipped: number; total: number; safe_to_ship: boolean }[]>(r),
    ),
  getRegressionSchedule: () =>
    af("/v1/regression/schedule").then((r) =>
      j<{ enabled: boolean; interval_hours: number | null;
          last_run_ts: string | null; ee_active: boolean }>(r),
    ),
  putRegressionSchedule: (enabled: boolean, intervalHours: number, config: Record<string, unknown>) =>
    af("/v1/regression/schedule", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ enabled, interval_hours: intervalHours, config }),
    }).then((r) => j<{ enabled: boolean; interval_hours: number }>(r)),
  licenseStatus: () =>
    af("/v1/license/status").then((r) =>
      j<{ active: boolean; org?: string; tier?: string; expiry?: string }>(r),
    ),

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

  // ── governed node: a real axor-core IntentLoop wired to the plane ──────────
  spawnGoverned: () =>
    af("/axor/governed/spawn", { method: "POST" }).then(
      (r) => j<{ node_id: string; run_id: string; events: number; denials: number; ttl_seconds: number }>(r),
    ),

  // ── demo: seed adapter-fidelity runs (recorded verdicts + provenance) ──────
  seedAdapterRuns: () =>
    af("/v1/demo/seed-adapter-runs", { method: "POST" }).then(
      (r) => j<{ seeded: string[]; config: Record<string, unknown> }>(r),
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
