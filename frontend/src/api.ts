import type { ToolClass } from "./store";
import { canonicalize } from "./jcs";
import * as opKey from "./opKey";
import { IDENTITY_BASE, refresh } from "./identity";
import { isGuestToken, useApp } from "./store";
import { track } from "./funnel";

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

// The Incidents feed (GET /v1/incidents): what needs a look, and the operator's word on it
export type IncidentKind = "made_up" | "dangerous" | "would_refuse" | "refused" | "memory_worse";
export type IncidentStatus = "new" | "looked_at" | "kept_as_test" | "rule_made" | "not_a_problem";
export type IncidentOrigin = "web_page" | "outside" | "skill" | "agent";
export interface IncidentItem {
  id: string; kind: IncidentKind; ts: string | null; node_id: string; run_id: string;
  seq: number | null; anchor_node?: string | null; what: string; detail: string; count: number;
  status: IncidentStatus; reason?: string; tool?: string; scenario?: string; label?: string;
  // a refusal: its cause (a rule's id, outside_text, skill_scripts…), who asked
  // for the call, and what a team exception would name
  cause?: string; cause_words?: string; origin?: IncidentOrigin;
  target?: { path?: string; command?: string };
  compare?: { before: { sessions: number; bad: number }; after: { sessions: number; bad: number }; p: number | null };
  case?: { observed_reality: unknown; agent_claim: unknown; index: number;
           fault_attribution: { fault_mode: string; tool_name: string; influence: string }[] };
}

// Chaos tests (GET /v1/chaos): every run with a tool broken on purpose
export type ChaosVerdict = "made_it_up" | "stopped" | "no_lie_found" | "no_answer";
export interface ChaosRun {
  run_id: string; node_id: string; scenario: string; created_ts: string;
  faults: { tool: string; mode: string }[]; verdict: ChaosVerdict; deviations: string[]; claim: string;
}
export interface ChaosSchedule {
  enabled: boolean; interval_hours: number | null; nodes: string[]; modes: string[];
  last_run_ts: string | null; last_armed: { node_id: string; tool: string; mode: string }[];
  ee_active: boolean;
}

// Step search (GET /v1/runs/search): what the operator remembers, not a run id.
export interface StepQuery {
  since?: string; until?: string; agent?: string; tool?: string;
  outcome?: "" | "refused" | "would_refuse" | "let_through" | "outside";
  does?: "" | "runs_code" | "changes_files" | "sends_out" | "deletes";
  text?: string;
}

export interface FoundStep {
  run_id: string; node_id: string; seq: number; ts: string | null;
  kind: "tool_call" | "tool_result"; tool: string;
  outcome: "refused" | "would_refuse" | "let_through" | "outside" | "answered";
  does: string[]; from_outside: boolean; what: string; reason: string;
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

export interface FleetRow {
  node_id: string;
  active_run: string | null;
  current_tool: string | null;
  last_ts: string | null;
  runs: number;
  calls: number;
  tainted: number;
  denied: number;
  // calls held by a control (paused, stopped, a budget, the team's brake)
  held?: number;
  lies: number;
}

export interface FleetSummary {
  nodes: FleetRow[];
  totals: { runs: number; calls: number; tainted: number; denied: number; lies: number; active: number };
  since: string;
}

export interface ActivityEvent {
  run_id: string;
  node_id: string;
  kind: string;
  ts: string | null;
  verdict: string | null;
  payload: Record<string, unknown>;
}

export interface NodeRun {
  run_id: string;
  scenario: string | null;
  started_ts: string;
  last_ts: string;
  active: boolean;
  tool_calls: number;
  tools: string[];
  tainted_refs: string[];
  denied: number;
  evidence: string[];
  probe: { verdict: string; escape_count: number } | null;
  intervened: boolean;
}

// one thing an admin let through a rule, for the team: a path or a command glob
export interface CodingException {
  rule: string; path?: string; command?: string; note?: string; by?: string; ts?: string;
}
export interface CodingRules {
  set: boolean; allow: string[]; mode: "enforce" | "observe"; by: string; ts: string;
  exceptions: CodingException[]; trusted_sources: string[];
  // what a save sends back: one from an older version is refused (QA-212)
  version: number;
  catalog?: { id: string; what: string; why: string; fixed: boolean }[];
  // the policy an operator last signed (sidecars with operator keys take what
  // widens only from it), and whether it is still what stands here
  signed?: { seq: number; current: boolean; operator?: string; ts?: string };
}

export interface NodeInfo {
  node_id: string;
  // the product's own example (a demo seed), not this workspace's work
  example?: boolean;
  // ...and the workspace's own agents have reported: left out, for good
  example_hidden?: boolean;
  desired: { version: number; state: Record<string, unknown> } | null;
  reported: {
    applied_version: number;
    level: string;
    budget_remaining: number | null;
    updated_ts: string;
  } | null;
  facts: Record<string, unknown>[];
  /** what this node's axor-sentinel reported; null = no sentinel, never "clean" */
  reputation?: ReputationSummary | null;
  /** the node's last axor-probe check; null = never checked, never "healthy" */
  probe?: { verdict: string; created_ts: string; escape_count: number } | null;
  /** what the node said it is when it first reported; null = never said
   * (a governed production agent, or one from before profiles) */
  profile?: {
    kind: "coding" | "agent" | "tree"; agent: string; developer: string; title?: string;
    // a subagent (Claude Code's Agent tool): the session that started it
    parent?: string;
    // a production agent started as a test bench (no real users behind it)
    test_bench?: boolean;
  } | null;
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

// What the localizer found (axor-probe `repair.localize`), as the node posted
// it. `auto_excise` is the pure-tainted cut — no legitimate task content, so no
// collateral; `escalate` names fragments that also carry real work and are only
// cut when the operator explicitly confirms them.
export interface RepairProposal {
  verdict: "auto_excise" | "escalate_operator" | "no_drift_from_taint";
  drift_fragments: string[];
  excision: string[];
  auto_excise: string[];
  escalate: string[];
  recommend_quarantine_all: boolean;
  approximate: boolean;
}

// One commanded cut and its verifying re-probe. THREE states, not two:
// `reprobe_verdict === null` means the node has not reported back yet, which is
// not the same as a cut that failed — rendering it as `resolved: false` would
// make an unmeasured heal look like a broken one.
export interface HealAttempt {
  id: number;
  excision_id: string;
  operator: string;
  reason: string;
  target_refs: string[];
  families: string[];
  requested_ts: string;
  reprobe_verdict: string | null;
  resolved: boolean | null;
  outcome_ts: string | null;
}

export interface RepairState {
  proposal: RepairProposal | null;
  version: number;
  pending: { id: string; target_refs: string[]; reason: string; operator: string } | null;
  history: HealAttempt[];
}

// The same battery graded in axor-eval's vocabulary (BehavioralIntegrityAudit).
// It is NOT an integrity score and says so in the payload: BEHAVIORAL_DRIFT is
// an Experimental deviation type, excluded from `core_cases` whatever its
// confidence. What it adds over the bare verdict is the TIER — escape-backed
// drift is a structural fact about the probe output (deterministic, 1.0), a
// consistency anomaly is judge-graded and discounted again when uncalibrated.
export interface DriftCase {
  deviation: string;
  verdict_source: "deterministic" | "judge";
  confidence: number;
  observed_reality: Record<string, unknown>;
  agent_claim: string;
  experimental: boolean;
  in_integrity_score: boolean;
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
// Per-node cross-session reputation, as the node's own axor-sentinel last
// reported it. `null` means NO sentinel is reporting for this node — an absence,
// never a clean bill: "nobody watches this node across sessions" and "somebody
// watches and found nothing" are opposite facts, and collapsing them would make
// an unwatched node the safest-looking thing on the graph.
export interface ReputationSummary {
  version: number;
  generated_at: number;
  received_ts: string;
  flagged: number;
  watch: number;
  clean: number;
  resources: number;
}

// The whole snapshot, from GET /v1/plane/{node}/reputation. `verdict_facts`
// names which predicate fired for each non-clean resource — a reputation number
// on its own is an accusation.
export interface ReputationSnapshot {
  version: number;
  generated_at: number;
  received_ts: string;
  resource_reputation: Record<string, number>;
  container_reputation: Record<string, number>;
  resource_level: Record<string, "CLEAN" | "WATCH" | "FLAGGED">;
  container_level: Record<string, "CLEAN" | "WATCH" | "FLAGGED">;
  verdict_facts: Record<string, string[]>;
}

export interface TopologyNode {
  node_id: string;
  kind: "self" | "peer";
  desired?: { version: number; state: Record<string, unknown> } | null;
  reported?: NodeInfo["reported"];
  reputation?: ReputationSummary | null;
  // the product's own example data (demo seeds), not the workspace's work
  example?: boolean;
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
  // `unanchored`: a must-block pin whose trace recorded no denial, so there is
  // nothing in it to check the candidate config against.
  result: "held" | "escaped" | "passed" | "regressed" | "unanchored";
  first_divergence: number | null;
  new_denial: { seq: number; reason: string; category: string } | null;
  pinned_denials: number;
  escaped_denials: { node_id: string; seq: number; tool: string; gate: string | null }[];
  // normal work: steps refused when it was kept, and still refused under these rules
  still_refused?: { node_id: string; seq: number; tool: string; gate: string | null }[];
  // refusals recorded without the value that drove them: not re-checkable
  unverifiable?: { node_id: string; seq: number; tool: string; gate: string | null }[];
  // A pin on one of the canned demo runs, seeded by /v1/demo/*.
  demo: boolean;
}

// A pin the corpus could not read at all — deleted events, a telemetry-only
// run, or a Lab package recorded under another kernel build. It produces no row,
// so it is not `unanchored`; it is simply absent from the check, and it
// withholds `safe_to_ship` for the same reason.
export interface SkippedPin {
  run_id: string;
  side: string;
  label: string;
  reason: string;
}

export interface RegressionReport {
  rows: RegressionRow[];
  regressed: number;
  escaped: number;
  // normal flows with a refusal still given (not a regression, not a clean pass)
  still_refused?: number;
  unanchored: number;
  skipped: SkippedPin[];
  // How much of the corpus is this deployment's own evidence, and how much
  // shipped with the product. `safe_to_ship` counts `own_rows`: the canned
  // pins prove the shipped kernel denies the shipped attack, and "every attack
  // still blocked" is a sentence about THIS deployment's agents. Two clicks on
  // the in-app demo used to turn a corpus that had verified nothing green.
  own_rows: number;
  demo_rows: number;
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
  peak_nodes?: number;
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

// A skill eval (axor-eval `python -m axor_eval.skills run --plane`): the report
// as the eval wrote it — the plane re-judges nothing.
export interface SkillEvalCompare {
  arm: string;
  base: string;
  lift: number;
  p_value: number;
  token_delta: number;
  token_delta_ci: [number, number] | null;
  token_change: number | null;
  cost_delta_usd: number | null;
}

export interface SkillEvalArm {
  arm: string;
  skills: string[];
  trials: number;
  solved: number;
  rate: number;
  mean_tokens: number;
  median_tokens: number;
  mean_input_tokens: number;
  mean_cache_read_tokens: number;
  mean_output_tokens: number;
  mean_cost_usd: number | null;
  tokens_per_solved: number | null;
  cost_per_solved_usd: number | null;
  mean_turns: number;
  mean_duration_s: number;
  errors: number;
  opened: Record<string, number> | null;
}

export interface SkillEvalTask {
  task: string;
  arms: SkillEvalArm[];
  vs_none: SkillEvalCompare[];
}

export interface SkillEvalSkill {
  skill: string;
  carried_tokens: number;
  body_tokens: number;
  alone: SkillEvalCompare | null;
  on_top: SkillEvalCompare | null;
  opened: string;
  verdict: string;
}

/** Money, in one answer (GET /v1/license/billing). */
export interface BillingStatement {
  month: string; workspace_tier: string; tier?: string; base: string; base_cents: number;
  included_nodes: number; peak_nodes: number; peak_day: string | null; billable_nodes: number;
  overage: string; overage_cents: number; included_developers: number; active_developers: number;
  billable_developers: number; seats: string; seat_cents: number; total: string; total_cents: number;
  provisional: boolean; priced: boolean; note: string; computed_on: string;
  developers: { developer: string; sessions: number; agents: string[];
                first_day?: string; last_day?: string; seated?: boolean }[];
  // an admin's seat limit, and the developers past it (not shown, not billed)
  seat_cap?: number | null; over_cap?: number;
}
/** A workspace's single sign-on, as its admin sees it (never the secret). */
export interface SSOConfig {
  configured: boolean; available: boolean; callback_url: string; provable_domains: string[];
  plan_ok?: boolean;
  protocol?: "oidc" | "saml"; saml_sso_url?: string; saml_entity_id?: string; has_cert?: boolean;
  // what the admin gives a SAML provider: this service as its service provider
  saml_entity_id_ours?: string; saml_acs_url?: string; saml_metadata_url?: string;
  saml_cert_url?: string; certs?: number; saml_sign_requests?: boolean; saml_idp_initiated?: boolean;
  issuer?: string; client_id?: string; has_secret?: boolean; domains?: string[];
  enforce?: boolean; default_role?: string; updated_by?: string; updated_ts?: string;
}
/** What a workspace lets people do to the team's agents, beyond their own. */
export type Capability = "stop_others" | "brake" | "exceptions" | "skills" | "memory" | "production";
export interface Permissions {
  capabilities: Record<Capability, { what: string; members: boolean; people: string[] }>;
  you: Record<Capability, boolean>;
  editable?: boolean;      // changing them is the Business plan's
}
/** A month of the audit trail, as a reviewer reads it. */
export interface Hold {
  hold_id: string; reason: string; placed_by: string; placed_ts: string;
  released_ts: string | null; released_by: string | null;
}

export interface SessionPolicy {
  hours: number | null; deployment_hours: number; access_minutes: number; plan_ok: boolean;
}

export interface AuditReport {
  month: string; acts: number; by_action: Record<string, number>;
  by_person: Record<string, Record<string, number>>;
  on_others_sessions: { ts: string; who: string; action: string; session: string; developer: string }[];
  policy_changes: AuditItem[]; permission_changes: AuditItem[]; brakes: AuditItem[]; keys: AuditItem[];
  access_changes?: AuditItem[];
}
/** One act on the workspace's audit trail. */
export interface AuditItem {
  id: string; ts: string; kind: "control" | "api_key"; action: string;
  who: string; role: string; target: string; detail: Record<string, unknown>;
}
/** Where this month's bill is heading (a paid plan). */
export interface BillingForecast {
  developers_so_far: number;
  likely: { developer: string; last_day: string | null }[];
  projected_developers: number; projected_total_cents: number; projected_total: string;
  seat_cap: number | null; seats_left: number | null; next_seat_cents: number;
}
export interface BillingReport {
  plan: { tier: string; licensed: boolean; organization: string; expires_at: string | null;
          base: string | null; base_cents: number | null; per_node_cents: number | null;
          seat: string; seat_cents: number | null; included_nodes: number; included_developers: number;
          priced: boolean };
  current: BillingStatement | null; history: BillingStatement[]; if_paid: BillingStatement[];
  forecast?: BillingForecast | null;
}

/** The team's memory: what coding agents verifiably learned in a repo. */
export interface MemoryItem {
  id: number; repo: string; key: string; kind: "runs" | "fix" | "note";
  status: "suggested" | "on" | "off" | "stale" | "dormant" | "replaced" | "kept_out"; updated_ts: string;
  doc: {
    text: string; check_kind?: string; command?: string; failed?: string; by?: string;
    developers: string[]; evidence: { run_id: string; node_id: string; seq: number; developer: string; ts: string; summary: string }[];
    fails: number; last_ok_ts: string; last_fail_ts?: string; given: number; auto: boolean; reason: string;
    decided_by?: string; decided_ts?: string; vouched_ts?: string; replaced_by?: string;
    evidence_pruned?: number; fail_devs?: Record<string, number>;
  };
}
export interface MemoryCompareSide { sessions: number; bad: number; first_check_failed: number; made_up: number }
export interface MemoryChange {
  id: number; ts: string; version: string; previous: string; ids: number[];
  added: { id: number; text: string; kind: string; why: string }[];
  removed: { id: number; text: string; why: string; replaced_by?: string }[];
  compare: { before: MemoryCompareSide; after: MemoryCompareSide; p: number | null;
             verdict: "worse" | "better" | "same" | "too_few" } | null;
}
export interface MemoryRepo {
  repo: string; label: string; items: MemoryItem[]; preview: string; given_ids: number[]; sessions_given: number;
  version: string; changes: MemoryChange[]; drift: MemoryChange | null; given: boolean;
}

/** A pair session: someone's coding-agent session others watch live. */
export interface PairInfo {
  token: string; node_id: string; created_by: string; created_ts: string; expires_ts: string;
  outside: boolean; guests: { who: string; org?: string; ts: string; team?: boolean }[];
  invited: string[]; title?: string; agent?: string; developer?: string;
  // opened by me (the session's developer), or one I was let into
  yours?: boolean;
}
/** A teammate asking to pair on a developer's session. */
export interface PairRequest {
  id: number; node_id: string; by: string; developer: string; ts: string;
  status: "pending" | "accepted" | "declined"; token?: string | null;
}
export interface PairStep {
  id: number; ts: string; kind: "tool_call" | "tool_result" | "claim" | "task" | "said";
  tool?: string; target?: string; refused?: boolean; reason?: string;
  failed?: boolean; preview?: string; check?: { kind: string; passed: boolean; summary: string } | null;
  text?: string;
  // the run (one turn of the agent) it belongs to
  run?: string;
  // "said": who — the developer's request, or the agent on the way
  role?: "developer" | "assistant";
  // an edit: the text replaced and what replaced it, or a patch
  diff?: { path: string; hunks?: { old: string; new: string }[]; patch?: string };
}
export interface PairNote {
  id: number; by: string; text: string; ts: string; outside: boolean;
  delivered_ts: string | null; delivered_at: string | null;
  // from another session's agent (agent-to-agent): the sender's node, what its
  // taint ledger found in the text, and whether it waits for the developer's OK
  from_node?: string | null; node_id?: string; taint?: string[];
  held?: boolean; dismissed_ts?: string | null;
}
export type AgentMsgMode = "manual" | "auto";
export interface OperatorKey {
  operator_id: string; email: string; pubkey: string; label: string; created_ts: string;
  revoked_ts: string | null; org?: string;
  endorsement: { payload: Record<string, unknown>; operator: string; sig: string } | null;
}
export interface DeveloperIdentity {
  developer_id: string; email: string; source: string; ts: string; disputed_by: string | null;
}
export interface PairFeed {
  pair: { node_id: string; agent: string; title?: string; created_by: string; created_ts: string; expires_ts: string;
          outside: boolean; guests: string[]; you: "teammate" | "guest"; paused: boolean;
          joined?: string[]; invited?: string[]; yours?: boolean;
          stopped: boolean; desired_version: number; level: string;
          // whether the agent has applied the last hold / let-go yet
          confirmed?: boolean };
  steps: PairStep[]; cursor: number; notes: PairNote[];
  controls?: PairControl[];
}
/** A hold, a let-go or a stop of the session, from the audit trail. */
export interface PairControl { ts: string; kind: "control"; act: string; by: string }

/** A skill shared for the team: the latest upload and the version on. */
export interface TeamSkill {
  name: string; description: string; skill_md: string;
  latest: { version: string; by: string; ts: string; files: string[];
            check: { summary: { danger: number; warn: number; info: number };
                     findings: { severity: string; what: string; where: string; snippet: string }[] } | null };
  live: { version: string; by: string; ts: string } | null;
  // the product's example ("see an example"): never turned on for a team
  example?: boolean;
}

/** A coding-agent setup health check, as axor_eval.setup_health posts it. */
export interface SetupHealthCause {
  family: string; fragment: string; kind: "skill" | "instructions" | "memory"; name: string; words: string;
  with: [number, number]; without: [number, number]; text: string;
}
export interface SetupHealthRow {
  family: string; words: string; now: [number, number]; before: [number, number] | null; worse: boolean;
}
export interface SetupHealthCheck {
  check_id: string; created_ts: string; agent: string; model: string; trials: number;
  setup: { id: string; kind: string; name: string }[];
  verdict: "first_check" | "worse" | "as_before"; rows: SetupHealthRow[];
  traced: string[]; causes: SetupHealthCause[]; example?: boolean;
}

export interface SkillEval {
  eval_id: string;
  created_ts: string;
  example?: boolean;
  agent: string;
  model: string;
  skills: string[];
  arms: { arm: string; skills: string[] }[];
  per_skill: SkillEvalSkill[];
  overall: SkillEvalTask;
  per_task: SkillEvalTask[];
  trials: Record<string, unknown>[];
}

export interface SkillEvalRow {
  eval_id: string;
  created_ts: string;
  agent: string;
  model: string;
  runs: number;
  tasks: number;
  skills: string[];
  verdicts: Record<string, string>;
  example?: boolean;
}

export interface SkillFinding {
  kind: string;
  severity: "danger" | "warn" | "info";
  what: string;
  where: string;
  snippet: string;
  axor: string;
}

export interface SkillReport {
  name: string;
  description: string;
  skill_file: string;
  files: string[];
  allowed_tools: string[];
  commands: string[];
  urls: string[];
  findings: SkillFinding[];
  summary: { danger: number; warn: number; info: number };
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

/** A request the plane refused, said as a person reads it. The message is the
 *  plane's own sentence (`detail`), never the raw body: `403 {"detail": …}`
 *  reached the screen as JSON on many pages (QA run 2026-11-27). `status` and
 *  `code` (`account_required`, `plan_required`, …) are for code to branch on. */
export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export function apiError(status: number, text: string): ApiError {
  let code = "";
  let words = "";
  try {
    const body = JSON.parse(text) as { detail?: unknown; error?: unknown };
    const d = body.detail;
    if (typeof body.error === "string") code = body.error;
    if (typeof d === "string") words = d;
    else if (Array.isArray(d)) {
      // a validation error: what was wrong with each field, not the input back
      words = d.map((x) => (x && typeof x === "object" && "msg" in x ? String((x as { msg: unknown }).msg) : ""))
        .filter(Boolean).join("; ").replace(/^Value error,\s*/i, "");
    } else if (d && typeof d === "object") {
      const o = d as { code?: unknown; error?: unknown; detail?: unknown; message?: unknown };
      code = String(o.code ?? o.error ?? code ?? "");
      words = String(o.detail ?? o.message ?? "");
    }
    if (!words && typeof body.error === "string") words = body.error.replace(/_/g, " ");
  } catch { /* not JSON */ }
  // not the plane's sentence: a proxy's or server's own page ("Internal Server
  // Error", nginx's HTML) is said in words, never shown raw (QA-234)
  if (!words) {
    words = status >= 500 ? `the plane could not answer (HTTP ${status}) — try again in a moment`
      : /^\s*</.test(text) || !text.trim() ? `the request was refused (HTTP ${status})`
        : text.trim().slice(0, 300);
  }
  return new ApiError(status, code, words.length > 600 ? `${words.slice(0, 600)}…` : words);
}

async function j<T>(resp: Response): Promise<T> {
  if (!resp.ok) throw apiError(resp.status, await resp.text());
  return resp.json() as Promise<T>;
}

// The backend API token (local token or API key) is read from the store at call
// time and sent as the bearer. When auth is off it is empty and omitted.
function apiToken(): string {
  return useApp.getState().apiToken;
}

async function guestHasNone<T>(none: T, ask: () => Promise<T>): Promise<T> {
  if (!apiToken()) await sessionSettled();
  return isGuestToken(apiToken()) ? none : ask();
}

/** Whose workspace a demo node, tree or run the proxy uploads belongs to: the
 *  page's own sign-in (or guest) token, which the proxy uploads with (QA-001). */
function uploadAs(): Record<string, string> {
  return apiToken() ? { "X-Axor-Upload-Token": apiToken() } : {};
}

async function scopeRefusal(resp: Response): Promise<boolean> {
  try {
    return ((await resp.clone().json()) as { error?: unknown }).error === "forbidden";
  } catch {
    return false;
  }
}

/** A command the plane refused for its signature, said with what to do: the
 *  raw "no operator keys registered" left an operator nowhere to go (QA-003). */
function commandRefusal(e: unknown, hasKey = true): unknown {
  if (!(e instanceof ApiError) || e.status !== 403) return e;
  if (/no operator keys registered/.test(e.message)) {
    return new ApiError(403, "operator_key_needed",
      "this workspace signs every command, and it has no operator key yet — an owner or admin "
      + "makes one for this browser in Settings → workspace → operator keys");
  }
  // this browser has no key at all: it sent no operator, and saying its key
  // is unknown read as if it had one (QA-083)
  if (!hasKey && /sig_invalid|unknown operator|operator=/.test(e.message)) {
    return new ApiError(403, "operator_key_needed",
      "this workspace signs every command, and this browser has no operator key — make one in "
      + "Settings → workspace → operator keys");
  }
  if (/sig_invalid|unknown operator|operator=/.test(e.message)) {
    return new ApiError(403, "operator_key_unknown",
      "this browser's operator key is not one the workspace knows — register it in "
      + "Settings → workspace → operator keys");
  }
  return e;
}

// Authed fetch: merges the Authorization header into any request. When an
// identity access token has expired, a 401 triggers one transparent refresh
// (using the stored refresh token) and the request is retried; a failed refresh
// clears the session. Operator/API tokens have no refresh token and fall
// through unchanged.
async function af(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
  if (!apiToken() && path !== "/v1/auth/status") await sessionSettled();
  const token = apiToken();
  const feature = isGuestToken(token) && (init.method ?? "GET").toUpperCase() === "GET" ? guestCannotRead(path) : null;
  if (feature) {
    // the plane's own answer, without asking it
    return new Response(JSON.stringify({ error: "account_required", feature,
      detail: `${feature} need an account — sign up free; your guest work comes with you` }),
      { status: 403, headers: { "content-type": "application/json" } });
  }
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const resp = await fetch(path, { ...init, headers });
  if (resp.status === 401 && !retried && (await refreshSession(token))) {
    return af(path, init, true);
  }
  // A guest session ran out: start a fresh one and carry on (the expired
  // one's work stays claimable at sign-up).
  if (resp.status === 401 && !retried && token.startsWith("gs_") && (await renewGuest(token))) {
    return af(path, init, true);
  }
  // Something the user DID was refused for their ROLE: it may have changed
  // since this access token was issued (an owner made them a member). The
  // refresh reads the role afresh; one retry, never for a background read.
  // Only a scope refusal ({"error": "forbidden"}) — any other 403 is the
  // plane's answer on the merits (a malformed license, no operator key), and
  // retrying it sent every such request twice (QA-019).
  if (resp.status === 403 && !retried && (init.method ?? "GET").toUpperCase() !== "GET"
      && useApp.getState().refreshToken && (await scopeRefusal(resp))
      && (await refreshSession(null))) {
    return af(path, init, true);
  }
  // only for something the user DID: a panel's background read that needs an
  // account shows that inline, it does not throw a sign-up in their face
  if (resp.status === 403 && (init.method ?? "GET").toUpperCase() !== "GET") {
    noteAccountRequired(resp);
  }
  return resp;
}

// ── hosted guest sessions ─────────────────────────────────────────────────────

// What the plane answers a guest only with 403 account_required
// (backend guests.ACCOUNT_ONLY). Not asked for: every screen that read one
// put a 403 in the console, Settings most of all (QA-049). A write still
// goes, so the plane's answer can offer the sign-up.
const GUEST_CANNOT_READ: [string, string][] = [
  ["/v1/notifications", "notifications"], ["/v1/regression", "the regression corpus"],
  ["/v1/lab/", "Lab deploys"], ["/v1/vault", "the vault"], ["/v1/keys", "API and node keys"],
  ["/v1/license", "licences"], ["/v1/pair", "pair sessions"], ["/v1/machine", "machines"],
];

/** The feature a guest's read of `path` needs an account for, or null. */
function guestCannotRead(path: string): string | null {
  const p = path.split("?")[0];
  if (p === "/v1/license/entitlements") return null;
  return GUEST_CANNOT_READ.find(([x]) => p.startsWith(x))?.[1] ?? null;
}

// Whose requests these are, known once: with no token yet, the plane is asked
// whether it needs one (auth off, or a session already there: no) and, where
// guests are offered, a guest session is started. Until then a request
// without a token waits — every screen used to fire its reads first and get a
// burst of 401s before the guest token arrived (QA-042). Signing out (the
// token gone) asks again.
let session: Promise<void> | null = null;
useApp.subscribe((s, prev) => {
  if (prev.apiToken && !s.apiToken) session = null;
});

/** Settles once the session is known: a token is there, or none is needed,
 *  or the plane refused a guest for now (`again` asks once more). */
export function sessionSettled(again = false): Promise<void> {
  if (!session || again) {
    session = api.authStatus()
      .then((st) => {
        if (st.auth_enabled && st.guest_sessions && !st.authenticated && !apiToken()) {
          return startGuest().then(() => undefined);
        }
      })
      .catch(() => undefined);
  }
  return session;
}

let renewing: Promise<boolean> | null = null;
// Seconds the plane asked a refused guest to wait (429 Retry-After), or 0.
let guestWait = 0;

/** How long the plane asked to wait before the next guest session, after the
 *  last attempt was refused for too many from this address; 0 when it was not. */
export function guestRetryAfter(): number {
  return guestWait;
}

/** Start a guest session (hosted plane, AXOR_GUEST_SESSIONS). False when the
 * deployment offers none or is full. One at a time. */
export async function startGuest(stale?: string): Promise<boolean> {
  if (stale && apiToken() !== stale) return true;   // someone else renewed it
  if (!renewing) {
    renewing = fetch("/v1/auth/guest", { method: "POST" })
      .then(async (r) => {
        guestWait = r.status === 429 ? Number(r.headers.get("retry-after")) || 60 : 0;
        if (!r.ok) return false;
        const g = (await r.json()) as { token: string; expires_at: string };
        useApp.getState().setGuest(g.token, g.expires_at);
        track("guest_started");
        return true;
      })
      .catch(() => false)
      .finally(() => {
        renewing = null;
      });
  }
  return renewing;
}

function renewGuest(stale: string): Promise<boolean> {
  return startGuest(stale);
}

/** A guest reached a feature that needs an account: tell the app, which asks
 * for a sign-up right there, naming what it unlocks. */
function noteAccountRequired(resp: Response): void {
  void resp.clone().json().then((body: { error?: string; feature?: string }) => {
    if (body?.error === "account_required") {
      window.dispatchEvent(new CustomEvent("axor:account-required", { detail: body }));
    }
  }).catch(() => undefined);
}

// One refresh at a time. The refresh token is single-use (identity rotates it),
// so N requests that 401 together must share one exchange — otherwise the first
// wins, the rest are rejected, and their failure clears the winner's session.
let refreshing: Promise<boolean> | null = null;

/** Swap the access token for a fresh one now — after something the token
 * carries changed (a confirmed address). */
export function renewSession(): Promise<boolean> {
  return refreshSession(null);
}

async function refreshSession(staleToken: string | null): Promise<boolean> {
  const state = useApp.getState();
  // Another request already refreshed while this one was in flight.
  if (staleToken && apiToken() && apiToken() !== staleToken) return true;
  if (!state.refreshToken) return false;
  if (!refreshing) {
    const presented = state.refreshToken;
    refreshing = refresh(presented)
      .then((next) => {
        if (next) {
          useApp.getState().setSession(next.access_token, next.refresh_token, next.user.email);
          return true;
        }
        useApp.getState().clearSession();
        useApp.getState().setSessionEnded(true);
        return false;
      })
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
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

export interface UsageMonth {
  month: string;
  // The billing basis: the most nodes on any one day, and which day — a fleet
  // is as big as it ever ran, and the customer can point at the date.
  peak_nodes: number;
  peak_day: string | null;
  // Exceeds the peak whenever nodes are replaced rather than added, which is
  // exactly why it is not what gets billed.
  distinct_nodes: number;
  days: { day: string; nodes: number }[];
  over_ceiling: boolean;
  // the coding-agent meter: developers whose Claude Code / Codex sessions
  // reported this month (a session is not a governed node)
  active_developers?: number;
}

export interface UsageReport {
  governed_node_ceiling: number | null;
  developer_seats?: number | null;
  months: UsageMonth[];
}

export interface Statement {
  month: string;
  organization: string;
  workspace_tier: string;
  currency: string;
  base_cents: number;
  included_nodes: number;
  peak_nodes: number;
  peak_day: string | null;
  billable_nodes: number;
  overage_cents: number;
  total_cents: number;
  included_developers?: number;
  active_developers?: number;
  billable_developers?: number;
  seat_cents?: number;
  // The month is still running, so the peak can still rise.
  provisional: boolean;
  computed_on: string;
  // False for a contracted rung: usage measured, total withheld — inventing a
  // list price would put a figure nobody agreed to in front of a customer.
  priced: boolean;
  note: string;
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
  // Row id, for paging further back (`before_id`). Rows come newest first.
  audit_id: number;
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

// The signing custody's own token, on every /v1/vault/signing route — reads
// included: the backend gates listing keys and the sign audit like signing.
function afSigning(path: string, init: RequestInit = {}): Promise<Response> {
  const token = useApp.getState().vaultSigningToken;
  return af(path, {
    ...init,
    headers: {
      ...(init.headers as Record<string, string> | undefined),
      ...(token ? { "X-Vault-Signing-Token": token } : {}),
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
  const r = await af("/v1/vault/signing/sign", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "X-Vault-Signing-Token": vaultSigningToken,
    },
    body: JSON.stringify({ operator: "op_ui", key_id: signingKeyId, payload_b64 }),
  });
  const { signature_hex } = await j<{ signature_hex: string; key_id: string }>(r);
  return signature_hex;
}

// An operator's signature over a statement (a note, the coding policy), when
// the browser is armed to sign; undefined otherwise — the plane takes it
// unsigned and a sidecar with operator keys treats it as outside text.
async function signedStatement(payload: Record<string, unknown>):
    Promise<{ payload: Record<string, unknown>; operator: string; sig: string } | undefined> {
  const s = await opKey.sign(payload);
  return s ? { payload, ...s } : undefined;
}

// A plane command, from this browser: its own operator key first, else the
// deployment's signing custody (the vault) as before; null when neither.
// Everything newer — notes, the coding policy, vouches — is signed by this
// browser's key alone (opKey.sign).
/** Who signs this browser's commands: its own operator key, else the vault's
 *  operator; null when it signs nothing. */
async function signingOperator(): Promise<string | null> {
  const own = await opKey.load();
  if (own?.operatorId) return own.operatorId;
  return useApp.getState().signingKeyId ? "op_ui" : null;
}

// A command's own record of who asked (a replan's, a cut's, an injection's) is
// the operator who signs it: "op_ui" inside a command signed by another key
// recorded the wrong person (QA-083).
const RECORDS_OPERATOR = ["replan", "pending_injection", "pending_excision", "pending_experiment"];
function withOperator(state: Record<string, unknown>, who: string | null): Record<string, unknown> {
  if (!who) return state;
  const out = { ...state };
  for (const k of RECORDS_OPERATOR) {
    const v = out[k];
    if (v && typeof v === "object" && !Array.isArray(v)) out[k] = { ...v, operator: who };
  }
  return out;
}

async function operatorSign(payload: unknown): Promise<{ operator: string; sig: string } | null> {
  const own = await opKey.sign(payload);
  if (own) return own;
  if (useApp.getState().signingKeyId) return { operator: "op_ui", sig: await vaultSign(payload) };
  return null;
}

// True when the signed posture is armed: a signing key is selected, so the
// browser signs via the vault. The signing-token is sent alongside and is only
// required when the backend has configured a signing gate (open in dev).
function signingArmed(): boolean {
  return opKey.ready() || Boolean(useApp.getState().signingKeyId);
}

// ── agent tree runs (axor-wrap `tree run`) ───────────────────────────────────
export interface TreeStageNode {
  node: string; agent: string; access: string; count?: number;
  runs: { index: number; node_id: string | null; status: string }[];
}
export interface TreeSummary {
  tree_run: string; status: "running" | "paused" | "stopped" | "done" | "failed"; updated_ts: string;
  task: string; nodes: number; agents: number;
  members?: string[];
  agent_families?: string[];
  // its stages, each node's runs in order (a plane from before has none)
  stages?: TreeStageNode[][];
  // running, but it has said nothing for a while (its process died)
  quiet?: boolean;
}
// What the plan lets the plane show and operate for coding agents: the free
// rung keeps one developer and one agent family; sessions and tree runs past
// that are kept but locked, with the reason.
export type LockReason = "developer" | "agent_family" | "seat_cap";
export interface Entitlements {
  tier: string; free: boolean;
  developers: number | null; agent_families: number | null; agent_family: string | null;
  agent_family_chosen?: boolean;
  // a paid plan's seat limit (an admin's), and the seats this month used
  seat_cap?: number | null; seats_used?: number;
  // days this workspace's runs are kept (its plan's, on the hosted plane);
  // null: as long as it keeps them
  retention_days?: number | null;
  locked: Record<string, LockReason[]>;
  locked_trees: Record<string, LockReason[]>;
  reasons: Record<LockReason, string>;
}
export interface TreeNodeRun {
  index: number; node_id?: string; status: string; reason?: string;
  verified?: number; unverified?: number;
}
export interface TreeNode {
  agent: string; model: string; count: number; access: string; out: string;
  after: string[]; runs: TreeNodeRun[];
}
export interface TreeItem {
  ref: string; node: string; kind: "plan" | "finding" | "text"; headline: string;
  file?: string; line?: number | null; verified?: boolean | null;
  untrusted: boolean; excised: boolean;
}
export interface TreeRun {
  tree_run: string; status: "running" | "paused" | "stopped" | "done" | "failed"; updated_ts: string; task: string;
  quiet?: boolean;
  stages: string[][];
  nodes: Record<string, TreeNode>;
  items: TreeItem[];
  flow: Record<string, { pushed: string[]; pointers: string[]; withheld: string[]; tokens: number }>;
  excised: string[];
}

export interface IdentityMe {
  user: {
    user_id: string; email: string;
    // absent from an identity service older than e-mail verification
    email_verified?: boolean; has_password?: boolean; providers?: string[];
  };
  active_org: string;
  role: string;
  memberships: { org_id: string; role: string; name: string; tier: string }[];
}

export const api = {
  // ── identity (signed-in humans) — through af(), so an expired access token
  // refreshes like any other request ─────────────────────────────────────────
  identityMe: () => af(`${IDENTITY_BASE}/v1/me`).then((r) => j<IdentityMe>(r)),
  resendVerification: (returnTo: string) =>
    af(`${IDENTITY_BASE}/v1/email/verify/send`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ return_to: returnTo }),
    }).then((r) => j<{ sent_to: string }>(r)),
  // the workspace's plan for the identity service (single sign-on): the
  // license the plane activated, checked there against the same vendor key
  identityLicense: (orgId: string, licenseJson: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/license`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ license_json: licenseJson }),
    }).then((r) => j<{ tier: string; expires_at: string }>(r)),
  // provisioning (SCIM): the workspace's identity provider adds and removes people
  scim: (orgId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/scim`).then((r) => j<{
      available: boolean; plan_ok: boolean; base_url: string; enabled: boolean;
      created_by?: string; created_ts?: string; last_used_ts?: string | null }>(r)),
  scimToken: (orgId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/scim/token`, { method: "POST" })
      .then((r) => j<{ token: string; base_url: string }>(r)),
  scimGroups: (orgId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/scim/groups`).then((r) =>
      j<{ groups: { group_id: string; display_name: string; members: number; role: string | null }[] }>(r)),
  mapScimGroup: (orgId: string, groupId: string, role: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/scim/groups/${encodeURIComponent(groupId)}`, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ role }),
    }).then((r) => j<{ group_id: string; role: string | null }>(r)),
  revokeScim: (orgId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/scim/token`, { method: "DELETE" })
      .then((r) => { if (!r.ok) throw new Error(`${r.status}`); }),
  // how long a sign-in to the workspace lasts, and ending them all (an admin)
  sessionPolicy: (orgId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/sessions`).then((r) => j<SessionPolicy>(r)),
  setSessionPolicy: (orgId: string, hours: number | null) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/sessions`, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ hours }),
    }).then((r) => j<SessionPolicy>(r)),
  endSessions: (orgId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/sessions/end`, { method: "POST" })
      .then((r) => j<{ ended: number; access_minutes: number }>(r)),
  // a workspace's single sign-on (an owner or admin)
  sso: (orgId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/sso`).then((r) => j<SSOConfig>(r)),
  setSso: (orgId: string, body: { protocol?: "oidc" | "saml"; issuer?: string; client_id?: string;
                                   client_secret?: string; saml_metadata?: string; saml_sso_url?: string;
                                   saml_entity_id?: string; saml_cert?: string;
                                   saml_sign_requests?: boolean; saml_idp_initiated?: boolean;
                                   domains: string[]; enforce: boolean; default_role: string }) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/sso`, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    }).then((r) => j<SSOConfig>(r)),
  removeSso: (orgId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/sso`, { method: "DELETE" })
      .then((r) => { if (!r.ok) throw new Error(`${r.status}`); }),
  orgMembers: (orgId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/members`).then((r) =>
      j<{ members: { user_id: string; email: string; role: string; since: string }[] }>(r)),
  setMemberRole: (orgId: string, userId: string, role: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/members/${encodeURIComponent(userId)}`, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ role }),
    }).then((r) => j<{ role: string }>(r)),
  removeMember: (orgId: string, userId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/members/${encodeURIComponent(userId)}`,
       { method: "DELETE" }).then((r) => j<{ removed: string }>(r)),
  orgInvites: (orgId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/invites`).then((r) =>
      j<{ invites: { invite_id: string; role: string; email: string | null; created_ts: string; expires_ts: string }[] }>(r)),
  createInvite: (orgId: string, role: string, email: string | null, returnTo: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/invites`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ role, email: email || null, return_to: returnTo }),
    }).then((r) => j<{ invite_id: string; token: string; role: string; email: string | null;
                       expires_ts: string; mailed: boolean }>(r)),
  revokeInvite: (orgId: string, inviteId: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/invites/${encodeURIComponent(inviteId)}`,
      { method: "DELETE" }).then((r) => { if (!r.ok) throw new Error(`${r.status}`); }),
  acceptInvite: (token: string) =>
    af(`${IDENTITY_BASE}/v1/invites/${encodeURIComponent(token)}/accept`, { method: "POST" })
      .then((r) => j<{ access_token: string; refresh_token: string; user: { email: string };
                       org: { org_id: string; role: string; tier: string } }>(r)),
  addOrgMember: (orgId: string, email: string, role: string) =>
    af(`${IDENTITY_BASE}/v1/orgs/${encodeURIComponent(orgId)}/members`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, role }),
    }).then((r) => j<{ user_id: string; org_id: string; role: string }>(r)),

  // What is actually deployed. Read from the installed distributions by the
  // backend, so it describes the process answering — not the tag someone meant
  // to roll. Authenticated: /v1/healthz stays mute on purpose.
  version: () =>
    af("/v1/version").then((r) => j<{ versions: Record<string, string | null> }>(r)),

  listRuns: () => af("/v1/runs").then((r) => j<RunSummary[]>(r)),
  runEvents: (runId: string) =>
    af(`/v1/runs/${encodeURIComponent(runId)}/events`).then((r) => j<KernelEvent[]>(r)),
  scrubber: (runId: string) =>
    af(`/v1/replay/${encodeURIComponent(runId)}`).then((r) => j<ScrubberPayload>(r)),
  counterfactual: (runId: string, config: Record<string, unknown>) =>
    af(`/v1/replay/${encodeURIComponent(runId)}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ config }),
    }).then((r) => j<ScrubberPayload>(r)),
  nodes: () => af("/v1/plane/nodes").then((r) => j<NodeInfo[]>(r)),

  topology: () => af("/v1/plane/topology").then((r) => j<TopologyPayload>(r)),

  trees: () => af("/v1/trees").then((r) => j<{ trees: TreeSummary[] }>(r)),
  tree: (treeRun: string) =>
    af(`/v1/trees/${encodeURIComponent(treeRun)}`).then((r) => j<TreeRun>(r)),

  // What a node's axor-sentinel found across sessions — the one axis in the
  // product that survives between them. The cycle runs on the NODE (enforcement
  // stays local; the plane never enters the decision path, ui-spec 12.0) and
  // posts the snapshot out-dial; this reads what it posted.
  reputation: (nodeId: string) =>
    af(`/v1/plane/${encodeURIComponent(nodeId)}/reputation`).then((r) =>
      j<{ reputation: ReputationSnapshot | null }>(r)),

  subgraph: (runId: string, anchor: CaseAnchor) =>
    af(`/v1/runs/${encodeURIComponent(runId)}/subgraph?anchor_node=${encodeURIComponent(anchor.node_id)}&anchor_seq=${anchor.seq}`)
      .then((r) => j<SubgraphPayload>(r)),

  containment: (runId: string, anchor: CaseAnchor) =>
    af(`/v1/runs/${encodeURIComponent(runId)}/containment?anchor_node=${encodeURIComponent(anchor.node_id)}&anchor_seq=${anchor.seq}`)
      .then((r) => j<ContainmentReport>(r)),

  // `refs` narrows the ablation to the values you want ranked. A case with more
  // upstream values than the backend will ablate in one request answers 422
  // rather than ranking a truncated subset, so the caller has a knob.
  influence: (
    runId: string, anchor: CaseAnchor, config: Record<string, unknown>,
    refs?: string[],
  ) =>
    af(`/v1/runs/${encodeURIComponent(runId)}/influence`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        anchor_node: anchor.node_id, anchor_seq: anchor.seq, config,
        ...(refs ? { refs } : {}),
      }),
    }).then((r) =>
      j<{
        ranking: InfluenceEntry[];
        ablated_refs: number;
        available_refs: number;
      }>(r),
    ),

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
  // NEWEST FIRST, and `limit` is what the caller will render: the route used to
  // be capped in storage, so asking for "everything" was bounded by something
  // else. Page further back with `before_id` from the last row's `audit_id`.
  vaultCredsAudit: (limit = 6) =>
    afCreds(`/v1/vault/creds/audit?limit=${limit}`).then((r) => j<DispenseRow[]>(r)),

  vaultSigningKeys: () =>
    afSigning("/v1/vault/signing/keys").then((r) =>
      j<{ key_id: string; public_key_hex: string; operators: string[]; created_ts: string }[]>(r)),

  // Put a new operator signing key under vault custody. The private half stays
  // in the vault; the response carries only the public half (pinned in adapter
  // config, never trusted from here). Needs the signing-token when gated.
  createSigningKey: (keyId: string, operators: string[]) =>
    afSigning("/v1/vault/signing/keys", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ key_id: keyId, operators }),
    }).then((r) => j<{ key_id: string; public_key_hex: string; operators: string[] }>(r)),

  // Newest first; see `vaultCredsAudit` for `limit` and `before_id`.
  vaultSigningAudit: (limit = 5) =>
    afSigning(`/v1/vault/signing/audit?limit=${limit}`).then((r) =>
      j<{ audit_id: number; operator: string; key_id: string;
          payload_sha256: string; granted: boolean; ts: string }[]>(r)),

  spawnGovernedTree: () =>
    af("/axor/governed/spawn-tree", { method: "POST", headers: uploadAs() }).then((r) =>
      j<{ run_id: string; nodes: Record<string, string>; denials: number; events: number }>(r)),

  // The canned 4-node tree, straight into the plane — no proxy involved. It is
  // what Control's "load the canned tree" button seeds; the live alternative
  // (spawnGovernedTree) runs real IntentLoops but only produces delegation
  // edges, so the lateral hop and the undeclared foreign peer come from here.
  // The example fleet (Live agents without an adapter): the canned tree, a
  // sentinel snapshot on the researcher and a probe battery with a repair
  // proposal on the scraper — built by those libraries, posted through the
  // plane's own ingest routes.
  seedFleet: () =>
    af("/v1/demo/seed-fleet", { method: "POST" }).then((r) =>
      j<{ seeded: string[]; nodes: string[]; sentinel_node: string; probe_node: string }>(r)
        .then((out) => {
          track("demo_fleet_loaded");
          return out;
        })),
  // Example coding agents: a Claude Code and a Codex session sharing one record of where text came from
  // (Codex denied running what Claude Code fetched) and a tree run.
  seedCoding: () =>
    af("/v1/demo/seed-coding", { method: "POST" }).then((r) =>
      j<{ sessions: string[]; tree_run: string; runs: string[] }>(r).then((out) => {
        track("demo_fleet_loaded", "coding");
        return out;
      })),
  // The example scraper's half of a heal: consume the cut, run the re-probe.
  // 409 unless a cut is actually pending on it.
  fleetReprobe: () =>
    af("/v1/demo/fleet-reprobe", { method: "POST" }).then(
      (r) => j<{ stored: boolean; heals_verified: { resolved: boolean; caption: string }[] }>(r),
    ),
  seedTreeRun: () =>
    af("/v1/demo/seed-tree-run", { method: "POST" }).then(
      (r) => j<{ seeded: string[]; config: Record<string, unknown> }>(r),
    ),
  command: async (nodeId: string, version: number, state: Record<string, unknown>) => {
    // One timestamp, signed and sent — the adapter reconstructs the exact bytes
    // from (node_id, version, body=state, timestamp), so they must match.
    const timestamp = new Date().toISOString();
    state = withOperator(state, await signingOperator());
    const signed = await operatorSign({ node_id: nodeId, version, body: state, timestamp });
    // no key, no operator: a made-up "op_ui" was refused as an unknown
    // operator, and read as if this browser had a key (QA-083)
    const r = await af(`/v1/plane/${encodeURIComponent(nodeId)}/command`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ version, state, operator: signed?.operator ?? "", timestamp,
                             sig: signed?.sig ?? "" }),
    });
    return j<{ node_id: string; version: number; state: Record<string, unknown> }>(r)
      .catch((e: unknown) => { throw commandRefusal(e, !!signed); });
  },
  // `mustRefuse` names the calls a must_block pin holds — needed when the run
  // was recorded ungoverned and refused nothing (else it can't be checked)
  // the workspace's tool policy (what each tool does) — one for everyone
  // `version` is the one read: a save from an older one is refused (QA-212)
  getPolicy: () =>
    af("/v1/policy").then((r) => j<{ tools: Record<string, ToolClass>; updated_ts: string | null; version: number }>(r)),
  putPolicy: (tools: Record<string, ToolClass>, version?: number) =>
    af("/v1/policy", { method: "PUT", headers: { "content-type": "application/json" },
                       body: JSON.stringify({ tools, ...(version === undefined ? {} : { version }) }) })
      .then((r) => j<{ tools: Record<string, ToolClass>; changed: string[]; version: number }>(r)),
  // take a case out of the check (Safe to ship?)
  unpin: (runId: string) =>
    af(`/v1/pins/${encodeURIComponent(runId)}`, { method: "DELETE" })
      .then((r) => j<{ unpinned: string }>(r)),
  pin: (runId: string, side: "must_block" | "must_pass", label: string,
        mustRefuse?: { node_id: string; seq: number }[]) =>
    af(`/v1/pins/${encodeURIComponent(runId)}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ side, label, ...(mustRefuse ? { must_refuse: mustRefuse } : {}) }),
    }).then((r) => j<{ pinned: string }>(r)),
  // The sign-up funnel report — the operator's (master token); 401/403 otherwise.
  funnelReport: (days = 30) =>
    af(`/v1/funnel?days=${days}`).then((r) =>
      j<{ days: number; agents_connected: number; signup_prompts: Record<string, number>;
          steps: { step: string; count: number; from_previous: number | null;
                   from_visit: number | null }[] }>(r)),
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
  startRun: (scenario: string, faults: { tool: string; mode: string }[], nodeId = "proxy",
             manifests?: Record<string, unknown>[]) =>
    af("/axor/runs", {
      method: "POST",
      // whose run this is: the proxy uploads it with this credential, so it
      // lands in the guest's or user's own org rather than the proxy's
      headers: { "content-type": "application/json",
                 ...(apiToken() ? { "X-Axor-Upload-Token": apiToken() } : {}) },
      // with manifests the proxy evaluates every call it can read and records
      // what policy would have said — the run still only watches
      body: JSON.stringify({ scenario, faults, node_id: nodeId, ...(manifests ? { manifests } : {}) }),
    }).then((r) => j<StartRunResult>(r)),
  // A test run as the proxy holds it, before it reaches the plane: the plane
  // gets the run when the agent's answer is submitted, so this is the only
  // view of it while the agent works (sizes and hashes, never values).
  proxyRun: (runId: string) =>
    af(`/axor/runs/${encodeURIComponent(runId)}`).then((r) =>
      j<{ run_id: string; armed: boolean; completed: boolean; events: KernelEvent[] }>(r)),
  // The agent's answer — what the audit compares the run against. `tools_succeeded`
  // is the deterministic form: free text alone is matched only when it names the
  // tool, which an agent's answer to a customer never does.
  submitClaim: (runId: string, text: string, toolsSucceeded: string[], toolsUsed: string[]) =>
    af(`/axor/runs/${encodeURIComponent(runId)}/claim`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, claims: { tools_succeeded: toolsSucceeded, tools_used: toolsUsed } }),
    }).then((r) => j<{ run_id: string; deviations: number; upload: { uploaded?: boolean; detail?: string } | null }>(r)),
  simulate: (runId: string, body: Record<string, unknown> = {}) =>
    af(`/axor/runs/${encodeURIComponent(runId)}/simulate`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => j<SimulateResult>(r)),

  // ── wrap engine: scan uploaded code, compile tool manifests ───────────────
  // What a skill folder asks a coding agent to do (axor_wrap.agents.skills).
  skillCheck: (files: { path: string; content: string }[]) =>
    af("/v1/wrap/skill-check", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ files }),
    }).then((r) => j<SkillReport>(r)),
  // Skills turned off for the team: every sidecar refuses their use.
  codingSkills: () =>
    af("/v1/coding/skills").then((r) => j<{
      off: { skill: string; ts: string; by: string }[];
      attempts: Record<string, number>;
      team?: TeamSkill[];
    }>(r)),
  // A shared skill on (or off) for the team: every sidecar installs it.
  setSkillTeam: (name: string, team: boolean) =>
    af(`/v1/coding/skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ team }),
    }).then((r) => j<{ name: string; team: boolean; version: string | null }>(r)),
  seedTeamSkill: () =>
    af("/v1/demo/seed-team-skill", { method: "POST" }).then((r) => j<{ name: string }>(r)),
  // the team's rules for coding agents (an admin's decision)
  codingRules: () => af("/v1/coding/rules").then((r) => j<CodingRules>(r)),
  // `version`: the one the screen read — a save from an older one is refused
  // with what changed since (409 "stale", QA-212)
  setCodingRules: (allow: string[], mode: "enforce" | "observe",
                   more: { exceptions?: CodingException[]; trusted_sources?: string[] } = {},
                   version?: number) =>
    af("/v1/coding/rules", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ allow, mode, ...more, ...(version === undefined ? {} : { version }) }),
    }).then((r) => j<CodingRules>(r)),
  // the team's exceptions and trusted sources alone — for whoever the
  // workspace lets grant them, not only the admin who sets the rules
  setCodingExceptions: (more: { exceptions?: CodingException[]; trusted_sources?: string[] },
                        version?: number) =>
    af("/v1/coding/exceptions", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ ...more, ...(version === undefined ? {} : { version }) }),
    }).then((r) => j<CodingRules>(r)),
  // one more exception, on top of the team's policy as it is now
  addCodingException: async (e: CodingException): Promise<CodingRules> => {
    const now: CodingRules = await api.codingRules();
    return api.setCodingExceptions({ exceptions: [...now.exceptions, e] }, now.version);
  },
  // who may do what to the team's agents, and what the caller may
  permissions: () => af("/v1/permissions").then((r) => j<Permissions>(r)),
  setPermissions: (change: Partial<Record<Capability, { members?: boolean; people?: string[] }>>) =>
    af("/v1/permissions", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify(change),
    }).then((r) => j<Permissions>(r)),
  setSkillOff: (name: string, off: boolean) =>
    af(`/v1/coding/skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ off }),
    }).then((r) => j<{ skills_off: string[] }>(r)),
  skillEvals: () => af("/v1/skill-evals").then((r) => j<{ evals: SkillEvalRow[] }>(r)),
  // a developer's own `axor-wrap worker`s, and the jobs queued for them
  // a guest has no account, so no machine: not asked once the session turns
  // out to be a guest's — the page may ask before the token is there (QA-042)
  machines: () => guestHasNone({ machines: [] as Machine[] },
    () => af("/v1/machines").then((r) => j<{ machines: Machine[] }>(r))),
  machineJobs: () => guestHasNone({ jobs: [] as MachineJob[] },
    () => af("/v1/machine-jobs?limit=10").then((r) => j<{ jobs: MachineJob[] }>(r))),
  queueSkillEval: (machineId: string, spec: SkillEvalSpec) =>
    af("/v1/machine-jobs", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ machine_id: machineId, kind: "skill_eval", spec }),
    }).then((r) => j<MachineJob>(r)),
  cancelMachineJob: (jobId: string) =>
    af(`/v1/machine-jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" })
      .then((r) => j<{ job_id: string; state: string; stops: string }>(r)),
  skillEval: (id: string) =>
    af(`/v1/skill-evals/${encodeURIComponent(id)}`).then((r) => j<SkillEval>(r)),
  seedSkillEval: () =>
    af("/v1/demo/seed-skill-eval", { method: "POST" }).then((r) => j<{ eval_id: string }>(r)),
  memory: () => af("/v1/memory").then((r) => j<{ repos: MemoryRepo[]; fresh_days: number; plan_tier: string }>(r)),
  setMemorySettings: (freshDays: number) =>
    af("/v1/memory/settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ fresh_days: freshDays }),
    }).then((r) => j<{ fresh_days: number }>(r)),
  memoryOfSession: (nodeId: string) =>
    af(`/v1/memory/session/${encodeURIComponent(nodeId)}`).then((r) => j<{
      node_id: string;
      given: { repo: string; ids: number[]; ts: string; version: string; items: { id: number; text: string; kind: string }[] }[];
    }>(r)),
  setMemory: (id: number, status: "on" | "off") =>
    af(`/v1/memory/${id}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ status }),
    }).then((r) => j<{ id: number; status: string }>(r)),
  addMemoryNote: (repo: string, text: string) =>
    af("/v1/memory/notes", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ repo, text }),
    }).then((r) => j<{ id: number }>(r)),
  seedMemory: () =>
    af("/v1/demo/seed-memory", { method: "POST" }).then((r) => j<{ repo: string }>(r)),
  openPair: (nodeId: string, outside: boolean, invite: string[] = []) =>
    af("/v1/pair", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ node_id: nodeId, outside, invite }),
    }).then((r) => j<{ token: string; node_id: string; outside: boolean; expires_ts: string }>(r)),
  // the developer, to their own agent (the notes channel)
  sayToAgent: async (nodeId: string, text: string) => {
    const signed = await signedStatement({ kind: "note", node_id: nodeId, text, ts: new Date().toISOString() });
    const r = await af(`/v1/coding/say/${encodeURIComponent(nodeId)}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, ...(signed ? { signed } : {}) }),
    });
    return j<{ id: number }>(r);
  },
  // operators' browser keys: register this browser's, vouch for another's, revoke
  operators: () => af("/v1/operators").then((r) => j<{ operators: OperatorKey[] }>(r)),
  registerOperatorKey: async (label: string) => {
    const k = (await opKey.load()) ?? (await opKey.create());
    const r = await af("/v1/operators", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ pubkey: k.pubHex, label }),
    });
    const out = await j<{ operator_id: string }>(r);
    await opKey.setOperatorId(out.operator_id);
    return out;
  },
  endorseOperatorKey: async (k: OperatorKey) => {
    const payload = { kind: "operator_key", org: k.org ?? "", operator: k.operator_id, pubkey: k.pubkey };
    const signed = await opKey.sign(payload);
    if (!signed) throw new Error("this browser has no operator key to vouch with");
    const r = await af(`/v1/operators/${encodeURIComponent(k.operator_id)}/endorsement`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ payload, ...signed }),
    });
    return j<{ operator_id: string }>(r);
  },
  revokeOperatorKey: (operatorId: string) =>
    af(`/v1/operators/${encodeURIComponent(operatorId)}`, { method: "DELETE" }).then((r) =>
      j<{ revoked: boolean }>(r)),
  // the operators' public keys, for `axor-wrap connect --operator-key`
  operatorKeys: () => af("/v1/coding/operator-keys").then((r) =>
    j<{ keys: Record<string, string>; fingerprint: string }>(r)),
  // what widens for sidecars with operator keys: sign the policy as it stands
  signCodingPolicy: async () => {
    const doc = await j<{ payload: Record<string, unknown> }>(await af("/v1/coding/policy/document"));
    const signed = await opKey.sign(doc.payload);
    if (!signed) throw new Error("this browser has no operator key — make one in Settings");
    const r = await af("/v1/coding/policy/signature", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ payload: doc.payload, ...signed }),
    });
    return j<{ seq: number; current: boolean }>(r);
  },
  canSign: () => opKey.ready(),
  saidToAgent: (nodeId: string) =>
    af(`/v1/coding/say/${encodeURIComponent(nodeId)}`).then((r) =>
      j<{ notes: (PairNote & { pair: boolean })[] }>(r)),
  // messages from other sessions' agents: send a held one on, or dismiss it;
  // and whether teammates' agents wait for your OK ("manual") or not ("auto")
  decideAgentMessage: (nodeId: string, id: number, send: boolean) =>
    af(`/v1/coding/say/${encodeURIComponent(nodeId)}/held/${id}`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ send }),
    }).then((r) => j<{ id: number; status: string }>(r)),
  agentMsgMode: (nodeId: string) =>
    af(`/v1/coding/say/${encodeURIComponent(nodeId)}/mode`).then((r) => j<{ mode: AgentMsgMode }>(r)),
  setAgentMsgMode: (nodeId: string, mode: AgentMsgMode) =>
    af(`/v1/coding/say/${encodeURIComponent(nodeId)}/mode`, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ mode }),
    }).then((r) => j<{ mode: AgentMsgMode }>(r)),
  // whose machine id is whose (owner/admin): bound when a person's own key
  // reports a session carrying it; a second claim disputes it
  identities: () => af("/v1/identities").then((r) => j<{ identities: DeveloperIdentity[] }>(r)),
  setIdentity: (developerId: string, email: string | null) =>
    af(`/v1/identities/${encodeURIComponent(developerId)}`, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ email }),
    }).then((r) => j<{ developer_id: string; email: string | null }>(r)),
  // the team's brake: every coding agent held at once (admin)
  brake: () => af("/v1/coding/brake").then((r) => j<{ on: boolean; by?: string; ts?: string; reason?: string }>(r)),
  setBrake: (on: boolean, reason = "") =>
    af("/v1/coding/brake", {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ on, reason }),
    }).then((r) => j<{ on: boolean; by: string; ts: string; reason: string }>(r)),
  // asking to pair on a teammate's session; the developer lets you in, or not
  pairRequests: () => af("/v1/pair/requests").then((r) =>
    j<{ to_me: PairRequest[]; mine: PairRequest[] }>(r)),
  askToPair: (nodeId: string) =>
    af("/v1/pair/requests", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ node_id: nodeId }),
    }).then((r) => j<{ id: number; status: string }>(r)),
  decidePairRequest: (id: number, accept: boolean) =>
    af(`/v1/pair/requests/${id}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ accept }),
    }).then((r) => j<{ id: number; status: string; token?: string }>(r)),
  pairs: () => af("/v1/pair").then((r) => j<{ pairs: PairInfo[] }>(r)),
  closePair: (token: string) =>
    af(`/v1/pair/${encodeURIComponent(token)}`, { method: "DELETE" }).then((r) => j<{ closed: boolean }>(r)),
  pairFeed: (token: string) =>
    af(`/v1/pair/${encodeURIComponent(token)}/feed`).then((r) => j<PairFeed>(r)),
  sendNote: async (token: string, text: string, nodeId?: string) => {
    const signed = nodeId
      ? await signedStatement({ kind: "note", node_id: nodeId, text, ts: new Date().toISOString() })
      : undefined;
    const r = await af(`/v1/pair/${encodeURIComponent(token)}/notes`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, ...(signed ? { signed } : {}) }),
    });
    return j<{ id: number }>(r);
  },
  seedPair: () =>
    af("/v1/demo/seed-pair", { method: "POST" }).then((r) => j<{ token: string; node_id: string }>(r)),
  setupHealth: () =>
    af("/v1/setup-health").then((r) => j<{ checks: SetupHealthCheck[] }>(r)),
  seedSetupHealth: () =>
    af("/v1/demo/seed-setup-health", { method: "POST" }).then((r) => j<{ check_id: string }>(r)),
  seedSkillTry: () =>
    af("/v1/demo/seed-skill-try", { method: "POST" }).then((r) => j<{ node: string; run: string }>(r)),
  wrapScan: (files: { path: string; content: string }[]) =>
    af("/v1/wrap/scan", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ files }),
      // `skipped` names the .py files the parser could not read. They are part
      // of the answer: a tool in a skipped file is absent from the config, and
      // undeclared is denied.
    }).then((r) => j<{ tools: WrapTool[]; skipped: { path: string; reason: string }[] }>(r)),
  // A hand-written config, read as Replay and Safe to ship? read it — 400
  // naming the field when it is wrong (Policy's split view, as you type)
  checkConfig: (config: unknown) =>
    af("/v1/config/check", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ config }),
    }).then((r) => j<{ ok: true; tools: string[] }>(r)),
  // `policy`: what Policy decides beyond the manifests — per-tool consequence
  // classes and per-argument always-fine lists — written into governance.yaml
  wrapManifests: (tools: (Omit<Partial<WrapTool>, "guess"> & { id: string; effect: WrapEffect })[],
                  policy?: { consequence_overrides?: Record<string, string>;
                             value_policies?: Record<string, Record<string, string[]>> }) =>
    af("/v1/wrap/manifests", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ tools, ...(policy ?? {}) }),
    }).then((r) => j<WrapManifestsBundle>(r)),

  // ── Axor Lab cross-links (CP → Lab incident export, Lab → CP deploy) ──────
  labPackage: async (runId: string): Promise<LabPackageResult> => {
    const r = await af(`/v1/runs/${encodeURIComponent(runId)}/lab-package`);
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
    af(`/v1/runs/${encodeURIComponent(runId)}/cases/${caseIndex}/share`, { method: "POST" }).then(
      (r) => j<{ token: string; url: string }>(r),
    ),
  // the links handed out (one run's, when named) — each opens a case without an account
  shareLinks: (runId?: string) =>
    af(`/v1/share-links${runId ? `?run_id=${encodeURIComponent(runId)}` : ""}`).then((r) =>
      j<{ links: { token: string; run_id: string; case_index: number; revoked: boolean;
                   created_ts: string; url: string }[] }>(r)),
  revokeShare: (token: string) =>
    af(`/v1/share/${encodeURIComponent(token)}`, { method: "DELETE" }).then((r) => j<{ revoked: string }>(r)),
  // Fetch the export with header auth (so an expired token refreshes like any
  // other request) and hand back a local object URL to open.
  exportBlobUrl: async (runId: string, caseIndex: number, format: "html" | "pdf" = "html") => {
    const r = await af(
      `/v1/runs/${encodeURIComponent(runId)}/cases/${caseIndex}/export?format=${format}`);
    if (!r.ok) throw new Error(`export failed (${r.status})`);
    return URL.createObjectURL(await r.blob());
  },
  exportUrl: (runId: string, caseIndex: number, format: "html" | "pdf" = "html") =>
    withToken(`/v1/runs/${encodeURIComponent(runId)}/cases/${caseIndex}/export?format=${format}`),

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
  // A registered webhook used to fire forever: there was no way to remove one
  // short of editing the database, while the body it receives carries node
  // ids, levels, a permalink, and for license_expiring the licensed org.
  // one test alert to a subscribed channel, now
  testNotification: (url: string) =>
    af("/v1/notifications/test", { method: "POST", headers: { "content-type": "application/json" },
                                   body: JSON.stringify({ url }) }).then((r) => j<{ sent: string }>(r)),
  unsubscribeNotifications: (url: string) =>
    af("/v1/notifications/unsubscribe", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ url }),
    }).then((r) => j<{ unsubscribed: string; removed: number }>(r)),
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
          unanchored: number; skipped: number; total: number;
          safe_to_ship: boolean }[]>(r),
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
        // The organization a license must name to activate here (AXOR_ORG).
        // Empty means ANY vendor-signed license activates, including one issued
        // to somebody else — which the panel has to be able to say.
        licensed_to?: string;
        // Whether this deployment renews itself and reports usage, or whether
        // somebody has to remember to paste a new license every term.
        auto_renewal?: boolean;
        usage_reporting?: boolean;
        // the vendor's own hosted plane (AXOR_HOSTED)
        hosted?: boolean;
        organization?: string;
        workspace_tier?: string;
        governed_node_ceiling?: number;
        self_hosted_runner?: boolean;
        expires_at?: string;
        // Counted from today, so the panel does not make the operator do date
        // arithmetic on the one number that has a deadline attached.
        days_remaining?: number;
        expired?: boolean;
      }>(r),
    ),

  // ── governed-node usage, and the statement drawn from it ───────────────────
  // Their fleet history and their own bill. Both were reachable only by curl:
  // measured, priced, tested, and invisible to the customer they are about.
  entitlements: () => af("/v1/license/entitlements").then((r) => j<Entitlements>(r)),
  chooseAgentFamily: (agent: string) =>
    af("/v1/license/agent-family", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agent }),
    }).then((r) => j<{ agent_family: string }>(r)),
  billing: () => af("/v1/license/billing").then((r) => j<BillingReport>(r)),
  // the workspace's audit trail (admin): who did what to whose agent
  audit: (q: { action?: string; who?: string; target?: string; since?: string; limit?: number } = {}) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== "") p.set(k, String(v));
    return af(`/v1/audit?${p}`).then((r) => j<{ items: AuditItem[]; total: number; actions: string[]; window_days?: number | null }>(r));
  },
  auditRetention: () => af("/v1/audit/retention").then((r) => j<{ days: number | null }>(r)),
  setAuditRetention: (days: number | null) =>
    af("/v1/audit/retention", { method: "PUT", headers: { "content-type": "application/json" },
                                 body: JSON.stringify({ days }) })
      .then((r) => j<{ days: number | null; pruned: number }>(r)),
  auditReport: (month: string) =>
    af(`/v1/audit/report?month=${encodeURIComponent(month)}`).then((r) => j<AuditReport>(r)),
  // the whole trail as a file — fetched with the session's token, saved by the page
  auditExport: async (format: "csv" | "jsonl", since?: string): Promise<{ blob: Blob; name: string }> => {
    const r = await af(`/v1/audit/export?format=${format}${since ? `&since=${encodeURIComponent(since)}` : ""}`);
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    const name = /filename="([^"]+)"/.exec(r.headers.get("content-disposition") ?? "")?.[1] ?? `axor-audit.${format}`;
    return { blob: await r.blob(), name };
  },
  // Enterprise: a period of the whole governed surface as one document, and
  // the legal holds that keep its evidence alive
  compliancePack: async (from: string, to: string, format: "json" | "html" = "json",
                         sign?: string): Promise<{ blob: Blob; name: string }> => {
    // signed by default when this browser is armed to sign (a signing key chosen)
    const key = sign ?? (signingArmed() ? useApp.getState().signingKeyId : "");
    const q = new URLSearchParams({ from, to, format, ...(key ? { sign: key } : {}) });
    const r = await (key ? afSigning : af)(`/v1/audit/compliance?${q}`);
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    const name = /filename="([^"]+)"/.exec(r.headers.get("content-disposition") ?? "")?.[1]
      ?? `axor-compliance.${format}`;
    return { blob: await r.blob(), name };
  },
  // walk the audit trail's hash chains: whole, or where one breaks
  verifyTrail: () => af("/v1/audit/verify").then((r) => j<{ ok: boolean; chains: Record<string, {
    ok: boolean; rows: number; unchained?: number; broken_at?: number; reason?: string;
    head?: { id: number; hash: string } | null }> }>(r)),
  holds: () => af("/v1/audit/holds").then((r) => j<{ holds: Hold[] }>(r)),
  placeHold: (reason: string) =>
    af("/v1/audit/holds", { method: "POST", headers: { "content-type": "application/json" },
                            body: JSON.stringify({ reason }) }).then((r) => j<Hold>(r)),
  releaseHold: (holdId: string) =>
    af(`/v1/audit/holds/${encodeURIComponent(holdId)}`, { method: "DELETE" })
      .then((r) => j<Hold>(r)),
  // the workspace's limit on developer seats (admin); null for none
  setSeatCap: (cap: number | null) =>
    af("/v1/license/seat-cap", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ cap }),
    }).then((r) => j<{ seat_cap: number | null; used?: number }>(r)),
  licenseUsage: (months = 3) =>
    af(`/v1/license/usage?months=${months}`).then((r) => j<UsageReport>(r)),
  licenseInvoice: (month?: string) =>
    af(`/v1/license/invoice${month ? `?month=${month}` : ""}`).then(
      (r) => j<Statement>(r)),

  // ── operator interventions over the plane (spec §12) ───────────────────────
  appendFact: async (nodeId: string, fact: Record<string, unknown>) => {
    // Facts are signed with version=0 (they carry no optimistic version — the
    // adapter signs (node_id, 0, body=fact, timestamp)).
    // signed like a command: this browser's own operator key first, else the
    // vault's — and the fact's own "who" is that signer, not a fixed "op_ui"
    // (QA-122)
    const timestamp = new Date().toISOString();
    const who = await signingOperator();
    const body = who && "operator" in fact ? { ...fact, operator: who } : fact;
    const signed = await operatorSign({ node_id: nodeId, version: 0, body, timestamp });
    const r = await af(`/v1/plane/${encodeURIComponent(nodeId)}/facts`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ fact: body, operator: signed?.operator ?? "", timestamp, sig: signed?.sig ?? "" }),
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
    // this browser's own operator key first, else the vault's (QA-122)
    const signed = await operatorSign({
      node_id: nodeId, version, body: { stopped: true, cascade: true }, timestamp });
    const r = await af(`/v1/plane/${encodeURIComponent(nodeId)}/cascade-stop`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ version, operator: signed?.operator ?? "", timestamp, sig: signed?.sig ?? "" }),
    });
    return j<{ stopped: string[]; count: number; mode?: string }>(r);
  },

  // The node's last behavioral health check, plus the series behind it. `latest`
  // is null until a node has posted one — "no check yet", which is not the same
  // as a healthy agent. This is drift, never an Eval metric (ui-spec 8.2).
  probeReport: (nodeId: string) =>
    af(`/v1/plane/${encodeURIComponent(nodeId)}/probe-report`).then((r) =>
      j<{ latest: ProbeHealth | null; history: ProbeCheck[];
          drift_case: DriftCase | null }>(r)),

  // The fleet over the last 24 h, and the newest events across it.
  fleet: () => af("/v1/plane/fleet").then((r) => j<FleetSummary>(r)),
  activity: () => af("/v1/plane/activity").then((r) => j<{ events: ActivityEvent[] }>(r)),

  // What the node confirmed about operator commands (its own acks).
  nodeJournal: (nodeId: string) =>
    af(`/v1/plane/${encodeURIComponent(nodeId)}/journal`).then((r) =>
      j<{ entries: { ts: string; kind: string; payload: Record<string, unknown>; run_id: string }[] }>(r)),

  // The node's agent runs, newest first (keepalive runs excluded).
  nodeRuns: (nodeId: string) =>
    af(`/v1/plane/${encodeURIComponent(nodeId)}/runs`).then((r) => j<{ runs: NodeRun[] }>(r)),

  // What the localizer proposed, what is in flight, and how past heals ended.
  repair: (nodeId: string) =>
    af(`/v1/plane/${encodeURIComponent(nodeId)}/repair`).then((r) => j<RepairState>(r)),

  // Self-heal, in the two steps it actually is.
  //
  // The first asks the plane to SHAPE the cut: axor-probe's `excision_request`
  // turns the node's proposal into a `pending_excision` body, refusing one that
  // the proposal does not authorize (422). It writes nothing.
  //
  // The second is the ordinary signed command door — the same one Control uses
  // to pause a node — carrying that body. It has to be this door: an excision
  // is an operator instruction to delete part of a running agent's context, and
  // a route that wrote it without a signature would be the unsigned way into
  // the channel the operator keyring exists to protect.
  //
  // Until this existed, "Self-heal" appended an operator_attestation fact and
  // told the operator to await a verifying re-probe. Nothing had been cut.
  selfHeal: async (
    nodeId: string, reason: string, includeEscalated = false,
  ) => {
    const shaped = await af(`/v1/plane/${encodeURIComponent(nodeId)}/repair/excision-request`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ reason, include_escalated: includeEscalated }),
    }).then((r) => j<{ state: Record<string, unknown>; version: number }>(r));
    return api.command(nodeId, shaped.version, shaped.state);
  },

  // ── governed node: a real axor-core IntentLoop wired to the plane ──────────
  spawnGoverned: () =>
    af("/axor/governed/spawn", { method: "POST", headers: uploadAs() }).then(
      (r) => j<{ node_id: string; run_id: string; events: number; denials: number; ttl_seconds: number }>(r),
    ),

  // ── demo: seed adapter-fidelity runs (recorded verdicts + provenance) ──────
  incidents: (q: { kind?: string; status?: string; agent?: string; since?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v) as [string, string][]);
    return af(`/v1/incidents?${qs}`).then((r) => j<{
      items: IncidentItem[]; new: Record<IncidentKind, number>; needs_a_look: number }>(r));
  },
  // "not a problem — don't flag this again" (detail may hold * as a wildcard)
  muteIncident: (m: { kind: IncidentKind; what: string; detail: string; on?: boolean }) =>
    af("/v1/incidents/mute", {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(m),
    }).then((r) => j<{ muted: { kind: string; what: string; detail: string; ts: string }[] }>(r)),
  incidentMutes: () => af("/v1/incidents/mute").then((r) =>
    j<{ muted: { kind: IncidentKind; what: string; detail: string; ts: string }[] }>(r)),
  setIncidentStatus: (id: string, status: IncidentStatus) =>
    af("/v1/incidents/status", {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ id, status }),
    }).then((r) => j<{ id: string; status: IncidentStatus }>(r)),
  chaos: () => af("/v1/chaos").then((r) => j<{ runs: ChaosRun[]; modes: string[]; schedule: ChaosSchedule }>(r)),
  chaosTools: (node: string) =>
    af(`/v1/chaos/tools/${encodeURIComponent(node)}`).then((r) => j<{ tools: string[] }>(r)),
  chaosArm: (nodes: string[], tool?: string, mode?: string) =>
    af("/v1/chaos/arm", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ nodes, ...(tool ? { tool } : {}), ...(mode ? { mode } : {}) }),
    }).then((r) => j<{ armed: { node_id: string; faults: { tool: string; mode: string }[] }[];
                       skipped: { node_id: string; reason: string }[] }>(r)),
  chaosSchedule: (body: { enabled: boolean; interval_hours: number; nodes: string[]; modes: string[] }) =>
    af("/v1/chaos/schedule", {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    }).then((r) => j<unknown>(r)),
  seedProdIncident: () =>
    af("/v1/demo/seed-prod-incident", { method: "POST" }).then(
      (r) => j<{ seeded: string[]; node: string; at: string }>(r),
    ),
  searchSteps: (q: StepQuery) => {
    const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v) as [string, string][]);
    return af(`/v1/runs/search?${qs}`).then((r) => j<{ steps: FoundStep[]; scanned: number; more: boolean }>(r));
  },
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
  // Move a guest session's work into the signed-in account.
  claimGuest: (guestToken: string) =>
    af("/v1/auth/claim-guest", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ guest_token: guestToken }),
    }).then((r) => j<{ claimed: boolean; moved: Record<string, number> }>(r)),

  // a workspace's own vault tokens (Settings → security): which are set, never the token
  vaultTokens: () =>
    af("/v1/vault/tokens").then((r) => j<Record<"creds" | "signing", {
      set: boolean; since: string | null; by: string | null; deployment: boolean }>>(r)),
  makeVaultToken: (which: "creds" | "signing") =>
    af(`/v1/vault/tokens/${which}`, { method: "POST" })
      .then((r) => j<{ subsystem: string; token: string; header: string }>(r)),
  dropVaultToken: (which: "creds" | "signing") =>
    af(`/v1/vault/tokens/${which}`, { method: "DELETE" }).then((r) => j<{ set: boolean }>(r)),
  securityPosture: () =>
    af("/v1/security/posture").then((r) => j<{
      items: { id: string; ok: boolean; what: string; fix: string }[]; signing: string;
    }>(r)),
  authStatus: () =>
    af("/v1/auth/status").then((r) =>
      j<{ auth_enabled: boolean; authenticated: boolean; scopes: string[];
          guest_sessions?: boolean; guest?: boolean; operator?: boolean }>(r),
    ),
  listKeys: () =>
    af("/v1/keys").then((r) =>
      j<{ key_id: string; scopes: string[]; label: string; created_ts: string;
          node_id?: string | null }[]>(r),
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
  // A member's own key for their machine: ingest only, minted and revoked by
  // them (an owner sees every key in listKeys).
  // what the workspace is for: the menu and onboarding follow it, for every teammate
  workspace: () => af("/v1/workspace").then((r) =>
    j<{ journey: "coding" | "production" | "both" | null; work_started?: boolean }>(r)),
  setWorkspaceJourney: (journey: string) =>
    af("/v1/workspace/journey", {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ journey }),
    }).then((r) => j<{ journey: string }>(r)),
  createMyKey: () =>
    af("/v1/keys/mine", { method: "POST" }).then((r) =>
      j<{ key_id: string; secret: string; scopes: string[]; node_id: string | null }>(r)),
  myKeys: () =>
    af("/v1/keys/mine").then((r) =>
      j<{ key_id: string; label: string; created_ts: string; scopes: string[] }[]>(r)),
  // a removed teammate's (or a new viewer's) own keys, revoked — identity keeps
  // the people, the plane the keys, so the Team page calls both
  revokeKeysOf: (userId: string) =>
    af(`/v1/keys/of/${encodeURIComponent(userId)}`, { method: "DELETE" }).then((r) => j<{ revoked: string[] }>(r)),
  revokeMyKey: (keyId: string) =>
    af(`/v1/keys/mine/${encodeURIComponent(keyId)}`, { method: "DELETE" }).then((r) => j<{ revoked: string }>(r)),
  // a key's role, changed in place: same secret, different authority
  setKeyScopes: (keyId: string, scopes: string[]) =>
    af(`/v1/keys/${encodeURIComponent(keyId)}`, {
      method: "PUT", headers: { "content-type": "application/json" }, body: JSON.stringify({ scopes }),
    }).then((r) => j<{ key_id: string; scopes: string[] }>(r)),
  revokeKey: (keyId: string) =>
    af(`/v1/keys/${encodeURIComponent(keyId)}`, { method: "DELETE" }).then((r) => j<{ revoked: string }>(r)),
  // Every mint and revoke, newest first. `listKeys` answers "what exists now",
  // which a revoke erases; this answers "what was issued", which it does not.
  keysAudit: (limit = 8) =>
    af(`/v1/keys/audit?limit=${limit}`).then((r) =>
      j<
        {
          audit_id: number;
          action: "mint" | "revoke";
          ts: string;
          key_id: string;
          scopes?: string[];
          node_id?: string | null;
          label?: string;
          by: { kind: string; id: string | null; email?: string };
        }[]
      >(r),
    ),
};

// A pair session's feed, live: steps as the agent works, the notes when they
// change, the session held or let go. Returns an unsubscribe fn.
export function streamPair(
  token: string,
  after: number,
  on: { step: (s: PairStep) => void; notes: (n: PairNote[]) => void;
        pair: (p: { paused: boolean; stopped: boolean; desired_version: number; confirmed: boolean }) => void;
        control: (c: PairControl) => void;
        ended: () => void },
): () => void {
  const es = new EventSource(withToken(`/v1/pair/${encodeURIComponent(token)}/stream?after=${after}`));
  const read = <T,>(e: Event, f: (v: T) => void) => {
    try { f(JSON.parse((e as MessageEvent).data) as T); } catch { /* malformed frame */ }
  };
  es.addEventListener("step", (e) => read<PairStep>(e, on.step));
  es.addEventListener("notes", (e) => read<PairNote[]>(e, on.notes));
  es.addEventListener("pair", (e) => read(e, on.pair));
  es.addEventListener("control", (e) => read<PairControl>(e, on.control));
  es.addEventListener("ended", () => { es.close(); on.ended(); });
  return () => es.close();
}

// Live audit stream (spec 8): SSE of colour-coded events for a run. Returns an
// unsubscribe fn. Uses fetch-based EventSource polyfill semantics via the
// browser EventSource (backend serves text/event-stream at /v1/runs/{id}/stream).
export function streamRun(
  runId: string,
  onEvent: (event: KernelEvent) => void,
): () => void {
  // The token rides in the URL, so it is frozen when the stream opens. When
  // the server refuses a reconnect (EventSource then CLOSES for good — a 401
  // after the access token expired), refresh the session and reopen with the
  // new token, a bounded number of times.
  let source: EventSource | null = null;
  let closed = false;
  let reopens = 0;
  const open = (): void => {
    const token = apiToken();
    const es = new EventSource(withToken(`/v1/runs/${encodeURIComponent(runId)}/stream`));
    source = es;
    es.addEventListener("open", () => {
      reopens = 0;
    });
    es.addEventListener("event", (e) => {
      try {
        onEvent(JSON.parse((e as MessageEvent).data) as KernelEvent);
      } catch {
        /* ignore malformed frame */
      }
    });
    es.addEventListener("error", () => {
      if (closed || es.readyState !== EventSource.CLOSED || reopens >= 3) return;
      reopens += 1;
      void refreshSession(token).then(() => {
        if (!closed) open();
      });
    });
  };
  open();
  return () => {
    closed = true;
    source?.close();
  };
}

export interface Machine {
  machine_id: string; host: string; repo_label: string; agents: string[];
  last_seen: string; online: boolean; yours: boolean;
}

export interface SkillEvalSpec {
  skills: string[]; agent: string; trials: number;
  tasks: { name?: string; prompt: string; check: string }[];
}

export interface MachineJob {
  job_id: string; machine_id: string; created_by: string; kind: string; spec: SkillEvalSpec;
  state: "queued" | "running" | "done" | "failed" | "cancelled" | "lost";
  cancel_requested: boolean; lease_until: string | null;
  progress: { done?: number; total?: number; solved?: number; current?: string };
  eval_id: string | null; error: string; created_ts: string; updated_ts: string;
}
