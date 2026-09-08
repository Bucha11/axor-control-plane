import { canonicalize } from "./jcs";
import { refresh } from "./identity";
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
}

// Provenance is scoped to one run: value refs are minted per trace from a
// counter that restarts at zero, so `v_ext_1` names a different value in every
// run and there is no edge that spans two of them.
export interface GraphKhop {
  focus: string;
  nodes: string[];
  edges: GraphEdge[];
}

// One fact holding a node down. `severity` indexes the degradation ladder
// (0=NORMAL..4=TERMINAL) and the node's level is max(severity) over the facts
// no attestation covers — the kernel's own recompute, not the plane's.
export interface DrivingFact {
  fact_id: string;
  fact_type: string;
  severity: number;
  reason: string;
  // The value branch the fact was recorded against, when the trace named one.
  causal_root: string | null;
  // Operators whose unrevoked attestation covers this fact. Empty = uncovered,
  // which is what makes it count toward the level.
  covered_by: string[];
}

export interface NodeCoverage {
  node_id: string;
  run_id: string | null;
  // What the node itself last reported. Never overwritten by attesting.
  reported_level: string;
  // What the level is once coverage is taken into account. Differs from
  // reported_level until the node applies the attestation off its stream.
  level: string;
  facts: DrivingFact[];
  covered: string[];
}

export interface BranchAttestation {
  fact_id: string;
  operator: string;
  reason: string;
  revokes: string | null;
  // Whether this coverage still stands. A revoked attestation stays in the
  // history — append-only, nothing is deleted — and reads `false`.
  in_effect: boolean;
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

// Behavioral health check (axor-probe, ui-spec 8.2). A family's state is
// deterministic: `escaped` iff a directional residual escaped on at least one
// probe of that type. `unprobed` is its own state — a family the battery never
// reached has no verdict, which is not a clean one. max_drift_score carries its
// UNCALIBRATED caveat in the field name so nothing here thresholds it.
export interface ProbeFamily {
  family: string;
  state: "clean" | "escaped" | "unprobed";
  escapes: number;
  probes: number;
}

export interface ProbeHealth {
  id: number;
  created_ts: string;
  session_id: string;
  agent_id: string;
  model: string;
  probe_library_version: string;
  overall_verdict: "CONSISTENT" | "DRIFT_DETECTED" | "INCONCLUSIVE" | "CONSISTENCY_ANOMALY";
  families: ProbeFamily[];
  probes_sent: number;
  probes_invalid: number;
  probes_triangulated: number;
  structural_failures: number;
  escape_count: number;
  escape_rate: number;
  escape_rate_ci: [number, number];
  calibration_status: string;
  max_drift_score_uncalibrated: number;
}

export interface ProbeCheck {
  id: number;
  created_ts: string;
  session_id: string;
  overall_verdict: string;
  escape_count: number;
  probes_sent: number;
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
  organization: string;
  workspace_tier: string; // "community" | "team" | "security"
  governed_node_ceiling: number;
  self_hosted_runner: boolean;
  expires_at: string;
  features: string[];
  live_nodes?: number;
  over_ceiling?: boolean;
  // True on every 200 — verified against the pinned key and stored. Kept
  // because "verified" and "active" being the same thing is worth asserting.
  activated?: boolean;
}

// Wrap engine (/v1/wrap): real code scan for the Config Builder. The engine is
// an optional backend extra — both routes answer 501 when it is not installed.
export interface WrapGuess {
  default_class: "READ" | "WRITE" | "EXPORT" | "EXEC" | "UNKNOWN";
  confidence: "high" | "medium" | "low";
  reason: string;
  driving_args: string[];
  untrusted_fields: string[];
}

export interface WrapTool {
  id: string;
  source: string;
  description: string;
  args_schema: Record<string, unknown>;
  framework: string;
  schema_confidence: string;
  guess: WrapGuess;
}

export interface WrapEffect {
  default_class: "READ" | "WRITE" | "EXPORT" | "EXEC";
  driving_args: string[];
  untrusted_fields?: string[];
  sensitive_fields?: string[];
}

export interface WrapManifestsBundle {
  manifests: Record<string, unknown>[];
  governance_yaml: string;
  wrap: Record<string, unknown>;
}

// ── Axor Lab cross-links ─────────────────────────────────────────────────────
// CP → Lab: a run exported as an axor-lab-incident/v1 package, or the honest
// list of reasons it cannot be. Lab → CP: an accepted cp-deploy package.
export type LabPackageResult =
  | { ok: true; pkg: Record<string, unknown> }
  | { ok: false; reasons: string[] };

export interface LabSkippedPin {
  run_id: string;
  trace_id: string;
  reason: string;
}

export type LabDeployResult =
  | {
      ok: true;
      package_id: string;
      pins_created: number;
      pins_replayable: number;
      pins_skipped: LabSkippedPin[];
      policy_stored: boolean;
      already_deployed: boolean;
    }
  | { ok: false; reasons: string[] };

export interface LabDeploySummary {
  package_id: string;
  created_ts: string;
  kernel: string;
  config_hash: string;
  parametric_config_hash: string;
  pins_created: number;
  manifest_count: number;
  source: { bundle_id?: string; condition_id?: string };
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

// Authed fetch: merges the Authorization header into any request. When an
// identity access token has expired, a 401 triggers one transparent refresh
// (using the stored refresh token) and the request is retried; a failed refresh
// clears the session. Operator/API tokens have no refresh token and fall
// through unchanged.
async function af(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
  const token = apiToken();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(path, { ...init, headers });
  if (resp.status === 401 && !retried) {
    const state = useApp.getState();
    if (state.refreshToken) {
      const next = await refresh(state.refreshToken);
      if (next) {
        state.setSession(next.access_token, next.refresh_token, next.user.email);
        return af(path, init, true);
      }
      state.clearSession();
    }
  }
  return resp;
}

// Append the token as a query param for URLs the browser opens directly (SSE
// via EventSource, export links in <a>) — those cannot carry a header.
function withToken(url: string): string {
  const token = apiToken();
  if (!token) return url;
  return url + (url.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
}

// base64 of the UTF-8 bytes of a string (btoa is latin1-only, so encode first).
function b64utf8(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export interface VaultCredential {
  tool: string;
  endpoint: string;
  version: number;
  revoked: boolean;
  scope_nodes: string[];
  header: string;
  scheme: string;
  // Whether this deployment can read this credential at all.
  sealed: boolean;
}

export interface VaultCredsHealth {
  enrolled: VaultCredential[];
}

export interface VaultEnrollment {
  tool: string;
  endpoint: string;
  secret: string;
  scope_nodes: string[];
  header: string;
  scheme: string;
}

export interface DispenseRow {
  node_id: string;
  tool: string;
  endpoint: string;
  version: number;
  run_id: string | null;
  seq: number | null;
  verdict: string | null;
  signed: boolean;
  principal: string;
  ts: string;
}

// The credential subsystem's own token, in its own header — the wall (spec v2
// Ch.5 §3). Sent on every /v1/vault/creds route and nowhere else, so a browser
// that can dispense still cannot request a signature.
function afCreds(path: string, init: RequestInit = {}): Promise<Response> {
  const token = useApp.getState().vaultCredsToken;
  return af(path, {
    ...init,
    headers: {
      ...(init.headers as Record<string, string> | undefined),
      ...(token ? { "X-Vault-Creds-Token": token } : {}),
    },
  });
}

// Seal a credential to the deployment's sealing key, in the operator's browser.
// libsodium's own sealed box (X25519 + XSalsa20-Poly1305), byte-identical to
// what the node opens with pynacl — no primitive is reimplemented here, and the
// wasm is loaded only when someone actually enrols something.
async function sealSecret(publicKeyHex: string, secret: string): Promise<string> {
  const sodium = (await import("libsodium-wrappers")).default;
  await sodium.ready;
  return sodium.to_base64(
    sodium.crypto_box_seal(sodium.from_string(secret), sodium.from_hex(publicKeyHex)),
    sodium.base64_variants.ORIGINAL,
  );
}

// Signed command posture (protocol §6): the browser canonicalizes the payload
// itself (byte-identical to the adapter's kernel), then asks the vault signing
// custody to sign exactly those bytes. The operator key never enters the
// browser — only the vault signing-token, which authorizes an audited sign
// request. Returns the signature hex to attach to the command/fact.
async function vaultSign(payloadObj: unknown): Promise<string> {
  const { signingKeyId, vaultSigningToken } = useApp.getState();
  const payload_b64 = b64utf8(canonicalize(payloadObj));
  const r = await fetch("/v1/vault/signing/sign", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Vault-Signing-Token": vaultSigningToken,
      ...(apiToken() ? { Authorization: `Bearer ${apiToken()}` } : {}),
    },
    body: JSON.stringify({ operator: "op_ui", key_id: signingKeyId, payload_b64 }),
  });
  const { signature_hex } = await j<{ signature_hex: string; key_id: string }>(r);
  return signature_hex;
}

// True when the signed posture is armed: a signing key is selected, so the
// browser signs via the vault. The signing-token is sent alongside and is only
// required when the backend has configured a signing gate (open in dev).
function signingArmed(): boolean {
  return Boolean(useApp.getState().signingKeyId);
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
    afCreds("/v1/vault/creds/health").then((r) => j<VaultCredsHealth>(r)),

  // Envelope mode (ui-spec §14.2). Registering a sealing PUBLIC key is what
  // stops this deployment storing plaintext at all; the private half is minted
  // by `axor-proxy vault keygen` on the operator's own machine and never enters
  // the browser — pasting it here would be handing over the one thing the mode
  // exists to keep away from the backend.
  vaultSealingKey: () =>
    afCreds("/v1/vault/creds/sealing-key").then(
      (r) => j<{ public_key_hex: string | null; envelope_mode: boolean }>(r)),
  registerSealingKey: (publicKeyHex: string) =>
    afCreds("/v1/vault/creds/sealing-key", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ public_key_hex: publicKeyHex }),
    }).then((r) => j<{ registered: boolean }>(r)),

  // Whose dispense attestations verify. Also a pubkey, also not a secret.
  vaultNodeKeys: () =>
    afCreds("/v1/vault/creds/node-keys").then((r) => j<Record<string, string>>(r)),
  registerNodeKey: (nodeId: string, publicKeyHex: string) =>
    afCreds("/v1/vault/creds/node-keys", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ node_id: nodeId, public_key_hex: publicKeyHex }),
    }).then((r) => j<{ registered: boolean }>(r)),

  // Enrol. In envelope mode the secret is sealed HERE, in the operator's own
  // browser, and only the ciphertext is posted — one step for the operator and
  // one fewer place the plaintext exists than a shell command would leave it.
  vaultEnroll: async (body: VaultEnrollment, sealingKey: string | null) => {
    const { secret, ...rest } = body;
    const payload: Record<string, unknown> = { ...rest };
    if (sealingKey) payload.sealed_secret = await sealSecret(sealingKey, secret);
    else payload.secret = secret;
    const r = await afCreds("/v1/vault/creds/enroll", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    return j<{ tool: string; endpoint: string; version: number }>(r);
  },
  vaultRotate: async (
    tool: string, endpoint: string, secret: string, sealingKey: string | null,
  ) => {
    const payload: Record<string, unknown> = { tool, endpoint };
    if (sealingKey) payload.sealed_secret = await sealSecret(sealingKey, secret);
    else payload.secret = secret;
    const r = await afCreds("/v1/vault/creds/rotate", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    return j<{ version: number }>(r);
  },
  vaultRevoke: (tool: string, endpoint: string) =>
    afCreds("/v1/vault/creds/revoke", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tool, endpoint }),
    }).then((r) => j<{ revoked: boolean; version: number }>(r)),

  // What every dispensed credential was fetched for. Never the credential.
  vaultCredsAudit: () =>
    afCreds("/v1/vault/creds/audit").then((r) => j<DispenseRow[]>(r)),

  vaultSigningKeys: () =>
    af("/v1/vault/signing/keys").then((r) =>
      j<{ key_id: string; public_key_hex: string; operators: string[]; created_ts: string }[]>(r)),

  // Put a new operator signing key under vault custody. The private half stays
  // in the vault; the response carries only the public half (pinned in adapter
  // config, never trusted from here). Needs the signing-token when gated.
  createSigningKey: (keyId: string, operators: string[]) =>
    fetch("/v1/vault/signing/keys", {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-Vault-Signing-Token": useApp.getState().vaultSigningToken,
        ...(apiToken() ? { Authorization: `Bearer ${apiToken()}` } : {}),
      },
      body: JSON.stringify({ key_id: keyId, operators }),
    }).then((r) => j<{ key_id: string; public_key_hex: string; operators: string[] }>(r)),

  vaultSigningAudit: () =>
    af("/v1/vault/signing/audit").then((r) =>
      j<{ operator: string; key_id: string; payload_sha256: string; granted: boolean; ts: string }[]>(r)),

  spawnGovernedTree: () =>
    af("/axor/governed/spawn-tree", { method: "POST" }).then((r) =>
      j<{ run_id: string; nodes: Record<string, string>; denials: number; events: number }>(r)),

  seedTreeRun: () =>
    af("/v1/demo/seed-tree-run", { method: "POST" }).then((r) => j<unknown>(r)),
  command: async (nodeId: string, version: number, state: Record<string, unknown>) => {
    // One timestamp, signed and sent — the adapter reconstructs the exact bytes
    // from (node_id, version, body=state, timestamp), so they must match.
    const timestamp = new Date().toISOString();
    const sig = signingArmed()
      ? await vaultSign({ node_id: nodeId, version, body: state, timestamp })
      : "";
    const r = await af(`/v1/plane/${nodeId}/command`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ version, state, operator: "op_ui", timestamp, sig }),
    });
    return j<{ node_id: string; version: number; state: Record<string, unknown> }>(r);
  },
  pin: (runId: string, side: "must_block" | "must_pass", label: string) =>
    af(`/v1/pins/${runId}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ side, label }),
    }).then((r) => j<{ pinned: string }>(r)),
  // The North-star surface: the regression corpus as counts (see GET /v1/pins).
  getPins: () =>
    af("/v1/pins").then((r) =>
      j<{
        pins: { run_id: string; side: string; label: string }[];
        must_block: number;
        must_pass: number;
        total: number;
      }>(r),
    ),
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

  // ── wrap engine: scan uploaded code, compile tool manifests ───────────────
  wrapScan: (files: { path: string; content: string }[]) =>
    af("/v1/wrap/scan", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ files }),
    }).then((r) => j<{ tools: WrapTool[] }>(r)),
  wrapManifests: (tools: (Omit<Partial<WrapTool>, "guess"> & { id: string; effect: WrapEffect })[]) =>
    af("/v1/wrap/manifests", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tools }),
    }).then((r) => j<WrapManifestsBundle>(r)),

  // ── Axor Lab cross-links (CP → Lab incident export, Lab → CP deploy) ──────
  labPackage: async (runId: string): Promise<LabPackageResult> => {
    const r = await af(`/v1/runs/${runId}/lab-package`);
    if (r.status === 422) {
      const body = (await r.json()) as { detail?: { reasons?: string[] } };
      return { ok: false, reasons: body.detail?.reasons ?? ["run is not convertible"] };
    }
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return { ok: true, pkg: (await r.json()) as Record<string, unknown> };
  },
  labDeploy: async (pkg: Record<string, unknown>): Promise<LabDeployResult> => {
    const r = await af("/v1/lab/deploy", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(pkg),
    });
    if (r.status === 422) {
      const body = (await r.json()) as { detail?: { reasons?: string[] } };
      return { ok: false, reasons: body.detail?.reasons ?? ["package rejected"] };
    }
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    return {
      ok: true,
      ...(await r.json()) as {
        package_id: string;
        pins_created: number;
        pins_replayable: number;
        pins_skipped: LabSkippedPin[];
        policy_stored: boolean;
        already_deployed: boolean;
      },
    };
  },
  labDeploys: () => af("/v1/lab/deploys").then((r) => j<LabDeploySummary[]>(r)),

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
      j<{
        active: boolean;
        // Whether the DEPLOYMENT pins a vendor public key (AXOR_VENDOR_PUBKEY).
        // Without one no license can be checked at all — a different problem
        // from "no license yet", with a different fix, and the panel has to be
        // able to say which.
        vendor_key_configured: boolean;
        organization?: string;
        workspace_tier?: string;
        governed_node_ceiling?: number;
        self_hosted_runner?: boolean;
        expires_at?: string;
      }>(r),
    ),

  // ── operator interventions over the plane (spec §12) ───────────────────────
  appendFact: async (nodeId: string, fact: Record<string, unknown>) => {
    // Facts are signed with version=0 (they carry no optimistic version — the
    // adapter signs (node_id, 0, body=fact, timestamp)).
    const timestamp = new Date().toISOString();
    const sig = signingArmed()
      ? await vaultSign({ node_id: nodeId, version: 0, body: fact, timestamp })
      : "";
    const r = await af(`/v1/plane/${nodeId}/facts`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ fact, operator: "op_ui", timestamp, sig }),
    });
    return j<{ appended: boolean }>(r);
  },
  // The blast-radius kill switch, and the third operator action the plane
  // verifies a signature for. It was the one the client did not sign: a bare
  // POST with no body, which a signed deployment answers 409 ("stale version
  // None") — so on exactly the deployments the vault exists for, Cascade stop
  // did not work at all. The dev posture has an empty keyring and takes the BFS
  // fallback, which ignores the body, so nothing ever surfaced it.
  //
  // The signed payload is the delta the backend rebuilds, not one the client
  // invents: `{stopped: true, cascade: true}` to the subtree ROOT — the tree
  // distributes the signal child-ward along spawn edges (spec v2 Ch.4 §6).
  cascadeStop: async (nodeId: string, version: number) => {
    const timestamp = new Date().toISOString();
    const sig = signingArmed()
      ? await vaultSign({
          node_id: nodeId,
          version,
          body: { stopped: true, cascade: true },
          timestamp,
        })
      : "";
    const r = await af(`/v1/plane/${nodeId}/cascade-stop`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ version, operator: "op_ui", timestamp, sig }),
    });
    return j<{ stopped: string[]; count: number; mode?: string }>(r);
  },

  // The node's last behavioral health check, plus the series behind it. `latest`
  // is null until a node has posted one — "no check yet", which is not the same
  // as a healthy agent. This is drift, never an Eval metric (ui-spec 8.2).
  probeReport: (nodeId: string) =>
    af(`/v1/plane/${nodeId}/probe-report`).then((r) =>
      j<{ latest: ProbeHealth | null; history: ProbeCheck[] }>(r)),

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

  // ── per-run value provenance & attestations (spec decision 6) ──────────────
  runProvenance: (runId: string, focus: string, k = 2, limit = 100) =>
    af(`/v1/runs/${encodeURIComponent(runId)}/provenance` +
       `?focus=${encodeURIComponent(focus)}&k=${k}&limit=${limit}`).then(
      (r) => j<GraphKhop>(r),
    ),
  nodeCoverage: (nodeId: string) =>
    af(`/v1/plane/${encodeURIComponent(nodeId)}/coverage`).then(
      (r) => j<NodeCoverage>(r),
    ),
  runAttestations: (runId: string, ref: string) =>
    af(`/v1/runs/${encodeURIComponent(runId)}/attestations` +
       `?ref=${encodeURIComponent(ref)}`).then(
      (r) => j<BranchAttestation[]>(r),
    ),

  // ── EE license (monetization 4) ────────────────────────────────────────────
  // The vendor public key is NOT sent: the trust root is deployment config
  // (AXOR_VENDOR_PUBKEY), and a signature checked against a key supplied in the
  // same request proves nothing. A 200 here means verified AND active.
  verifyLicense: (licenseJson: string) =>
    af("/v1/license/verify", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ license_json: licenseJson }),
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
  // `nodeId` binds the key to ONE governed node: the plane then refuses it for
  // any other, so a compromised node cannot forge its neighbour's heartbeat,
  // level or health verdict. Omit it for a fleet-wide operator key.
  createKey: (scopes: string[], label: string, nodeId?: string) =>
    af("/v1/keys", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(nodeId ? { scopes, label, node_id: nodeId } : { scopes, label }),
    }).then((r) =>
      j<{ key_id: string; secret: string; scopes: string[]; node_id: string | null }>(r),
    ),
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
