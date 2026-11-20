// Incidents — what needs a look, in one feed (GET /v1/incidents, backend
// incidents.py): an agent made something up (said a tool worked, said the
// tests passed…); a dangerous call was let through (deleted or wiped, sent out
// or ran what came from outside); a call the rules would have refused, let
// through in observe mode; and the refusals, grouped. Each has the operator's
// word on it — new, looked at, kept as a test, made a rule of, not a problem.
//
// The EvidenceCase is the product's artifact: a reproducible discrepancy
// between what a tool really returned and what the agent then claimed. Before
// this screen there was nowhere to see them together — each lived inside the
// run that produced it, reachable only if you knew the run id. Now the loop
// has a start and an inventory: catch produces these, explain opens one,
// prevent pins them.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Play, Zap } from "lucide-react";
import { api, EvidenceCaseDto, IncidentItem, IncidentKind, IncidentStatus } from "../api";
import { navigate } from "../router";
import { isConnected, useApp } from "../store";
import { C, MONO } from "../theme";


/** One line of whatever the agent claimed or the tool returned. */
export function brief(value: unknown, max = 90): string {
  if (value === null || value === undefined || value === "") return "—";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** What happened, in words — the deviation's name is for the receipt. */
const WHAT: Record<string, string> = {
  fabricated_tool_result: "said a failed tool worked",
  fabrication_contained: "a made-up result was stopped before it spread",
  tainted_value_exfiltrated: "tried to send out text that came from outside",
  corrupted_retrieval_used: "used a poisoned search result",
  direct_policy_violation: "followed an instruction from outside text",
  undisclosed_tool_substitution: "swapped a tool without saying so",
  budget_misreport: "misreported its budget",
  claimed_checks_passed: "said the checks passed when they failed",
};

const CHECK_WORDS: Record<string, string> = {
  tests: "THE TESTS", build: "THE BUILD", lint: "THE LINTER", typecheck: "THE TYPE CHECK",
};
/** A coding agent's check (tests, build…) the case is about, when it is one. */
function checkOf(ev: Pick<EvidenceCaseDto, "observed_reality">): { kind: string; command: string; summary: string } | null {
  const o = ev.observed_reality as Record<string, unknown> | null;
  return o && typeof o === "object" && typeof o.check === "string"
    ? { kind: o.check, command: String(o.command ?? ""), summary: String(o.summary ?? "") }
    : null;
}
export const incidentWhat = (deviation?: string | null): string =>
  deviation ? (WHAT[deviation] ?? deviation.replace(/_/g, " ")) : "something did not add up";

/** What the tool did, in words (the fault engine's mode names are for the receipt). */
const TOOL_DID: Record<string, string> = {
  silent_fail: "returned an error",
  corrupt_retrieval: "returned corrupted results",
  instruction_injection: "returned text with a hidden instruction in it",
  tool_substitution: "was swapped for a different tool",
  timeout: "timed out",
  empty_result: "returned nothing",
};
const toolDid = (mode: string) => TOOL_DID[mode] ?? mode.replace(/_/g, " ");

// where an incident came from: the plane's own examples, a test run, or work
const EXAMPLES = new Set(["adapter-demo", "multi-agent-demo", "coding-demo", "skill-demo", "ops-demo"]);
const TESTS = new Set(["tool-deprivation", "exfil", "injection"]);
function source(scenario?: string): string {
  if (scenario && EXAMPLES.has(scenario)) return "example data";
  if (scenario && TESTS.has(scenario)) return "a chaos test";
  return "live work";
}

const inlineLink: React.CSSProperties = {
  background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO,
  fontSize: 11.5, color: C.steel,
};

function when(ts: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

const KIND_CHIPS: [string, string][] = [
  ["made_up,dangerous,would_refuse", "needs a look"],
  ["made_up", "made something up"],
  ["dangerous", "dangerous, let through"],
  ["would_refuse", "would have been refused"],
  ["refused", "refused — stopped"],
  ["", "everything"],
];
const STATUS_WORDS: Record<IncidentStatus, string> = {
  new: "new", looked_at: "looked at", kept_as_test: "kept as a test",
  rule_made: "made a rule", not_a_problem: "not a problem",
};
const KIND_COLOR: Record<IncidentKind, string> = {
  made_up: C.red, dangerous: C.red, would_refuse: C.amber, refused: C.dim,
};

const chip = (on: boolean): React.CSSProperties => ({
  background: on ? "rgba(127,168,204,0.12)" : "none", border: `1px solid ${on ? C.steel : C.line}`,
  borderRadius: 14, color: on ? C.steel : C.mut, fontFamily: MONO, fontSize: 11, padding: "3px 10px",
  cursor: "pointer",
});
const small: React.CSSProperties = {
  background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut,
  fontFamily: MONO, fontSize: 11, padding: "4px 10px", cursor: "pointer",
};

/** The menu's count: new items that need a look. */
export function useIncidentCount(): number {
  const q = useQuery({ queryKey: ["incidents", "count"], queryFn: () => api.incidents({ status: "new" }),
                       refetchInterval: 30_000 });
  return q.data?.needs_a_look ?? 0;
}

export default function Evidence({ agent: agentParam }: { agent?: string } = {}) {
  const { mode } = useApp((s) => s.connection);
  const journey = useApp((s) => s.journey);
  const connect = useApp((s) => s.connect);
  const [kind, setKind] = useState("made_up,dangerous,would_refuse");
  const [status, setStatus] = useState("new");
  // `?agent=` — arriving from an agent's row in Agents
  const [agent, setAgent] = useState(agentParam ?? "");
  const feed = useQuery({
    queryKey: ["incidents", kind, status, agent],
    queryFn: () => api.incidents({ kind, status, agent }),
  });
  const all = useQuery({ queryKey: ["incidents", "all"], queryFn: () => api.incidents({}) });
  const agents = [...new Set((all.data?.items ?? []).map((i) => i.node_id))].sort();
  const items = feed.data?.items ?? [];
  const nothingAtAll = all.isSuccess && all.data.items.length === 0;

  return (
    <div style={{ maxWidth: 720, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: "0 0 6px" }}>Incidents</h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 16, lineHeight: 1.6 }}>
        what needs a look: an agent made something up (said a tool worked, said the tests passed when
        they failed), a dangerous call was let through, a call the rules would have refused ran
        anyway — and what the rules did refuse. from your{" "}
        <button onClick={() => navigate(isConnected(mode) ? "govern" : "connect")} data-testid="incidents-from-agents"
          style={inlineLink}>connected agents</button>' work
        {journey === "coding" ? <>. </> : <>, and from{" "}
          <button onClick={() => navigate("chaos")} data-testid="incidents-from-tests" style={inlineLink}>
            chaos tests</button>. </>}
      </div>

      {nothingAtAll ? (
        <div data-testid="evidence-empty"
          style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, padding: 20 }}>
          <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, marginBottom: 6 }}>No incidents yet.</div>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, lineHeight: 1.6, marginBottom: 16 }}>
            one appears the first time an agent says a tool worked when it didn't, a dangerous call goes
            through, or the rules refuse one. the fastest way to see one: test our example agent — we
            break its search on purpose and check what it says.
          </div>
          <div className="flex gap-2 flex-wrap">
            <button onClick={() => { connect("demo"); navigate("catch", { auto: "1" }); }}
              style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.green}`, borderRadius: 6, color: C.green, fontFamily: MONO, fontSize: 12, padding: "7px 14px", cursor: "pointer" }}>
              <Zap size={13} /> test the example agent
            </button>
            {!isConnected(mode) && (
              <button onClick={() => navigate("connect")}
                style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.steel}`, borderRadius: 6, color: C.steel, fontFamily: MONO, fontSize: 12, padding: "7px 14px", cursor: "pointer" }}>
                connect your agent <ArrowRight size={13} />
              </button>
            )}
          </div>
        </div>
      ) : (
        <>
          <div data-testid="incident-filters" className="flex flex-col gap-2 mb-4">
            <div className="flex gap-1.5 flex-wrap">
              {KIND_CHIPS.map(([k, label]) => (
                <button key={label} onClick={() => setKind(k)} style={chip(kind === k)}>
                  {label}{k && all.data ? ` ${k.split(",").reduce((n, x) => n + (all.data.new[x as IncidentKind] ?? 0), 0) || ""}` : ""}
                </button>
              ))}
            </div>
            <div className="flex gap-1.5 flex-wrap items-center">
              <button onClick={() => setStatus("new")} style={chip(status === "new")}>new</button>
              <button onClick={() => setStatus("")} style={chip(status === "")}>any status</button>
              <select aria-label="agent" value={agent} onChange={(e) => setAgent(e.target.value)}
                style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
                         fontFamily: MONO, fontSize: 11, padding: "3px 6px", marginLeft: 6 }}>
                <option value="">any agent</option>
                {agents.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
          </div>
          {feed.isPending ? (
            <div style={{ fontFamily: MONO, fontSize: 12, color: C.dim }}>loading…</div>
          ) : feed.isError ? (
            <div style={{ fontFamily: MONO, fontSize: 12, color: C.red }}>{(feed.error as Error).message}</div>
          ) : items.length === 0 ? (
            <div data-testid="incidents-none" style={{ fontFamily: MONO, fontSize: 12, color: C.mut, padding: "10px 0" }}>
              {status === "new" ? "Nothing new here — " : "Nothing here — "}
              <button onClick={() => { setStatus(""); setKind(""); }} style={inlineLink}>show everything</button>
            </div>
          ) : (
            <div className="flex flex-col gap-2" data-testid="evidence-list">
              {items.map((i) => <Item key={i.id} item={i} />)}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function StatusPick({ item }: { item: IncidentItem }) {
  const qc = useQueryClient();
  const set = useMutation({
    mutationFn: (st: IncidentStatus) => api.setIncidentStatus(item.id, st),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["incidents"] }),
  });
  return (
    <select aria-label={`status of ${item.id}`} value={item.status} data-testid="incident-status"
      onChange={(e) => set.mutate(e.target.value as IncidentStatus)}
      style={{ background: C.panel2, border: `1px solid ${item.status === "new" ? C.steel : C.line}`, borderRadius: 5,
               color: item.status === "new" ? C.steel : C.mut, fontFamily: MONO, fontSize: 11, padding: "3px 6px" }}>
      {(Object.keys(STATUS_WORDS) as IncidentStatus[]).map((st) => <option key={st} value={st}>{STATUS_WORDS[st]}</option>)}
    </select>
  );
}

function Item({ item }: { item: IncidentItem }) {
  const color = KIND_COLOR[item.kind];
  const toStep = () => navigate(`explain/${encodeURIComponent(item.run_id)}`,
    item.seq != null ? { seq: item.seq, node: item.anchor_node ?? item.node_id } : undefined);
  const title = item.kind === "made_up" ? incidentWhat(item.what)
    : item.kind === "dangerous" ? item.what
      : item.kind === "would_refuse" ? `${item.what} — would have been refused`
        : `${item.what} — refused${item.count > 1 ? ` ×${item.count}` : ""}`;
  const c = item.case;
  const fault = c?.fault_attribution?.[0];
  const check = c ? checkOf(c) : null;
  return (
    <div data-testid={item.kind === "made_up" ? "evidence-row" : "incident-row"} data-kind={item.kind}
      style={{ background: C.panel, border: `1px solid ${C.line}`, borderLeft: `3px solid ${color}`, borderRadius: 10, padding: 14 }}>
      <div className="flex items-center justify-between flex-wrap" style={{ gap: 8, marginBottom: 8 }}>
        <span className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 700, color }}>
          {item.kind !== "refused" && <AlertTriangle size={13} />}
          {title}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>{when(item.ts)}</span>
      </div>
      {c ? (
        <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 10, marginBottom: 10 }}>
          <div>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 4 }}>
              {check ? `${CHECK_WORDS[check.kind] ?? check.kind.toUpperCase()} · ${check.command}`
                : <>THE TOOL {fault ? `· ${fault.tool_name}` : ""}</>}
            </div>
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, wordBreak: "break-word" }}>
              {check ? `failed — ${check.summary || "the last run did not pass"}`
                : fault ? toolDid(fault.fault_mode) : brief(c.observed_reality)}
            </div>
          </div>
          <div>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 4 }}>
              THE AGENT SAID
            </div>
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, wordBreak: "break-word" }}>{brief(c.agent_claim)}</div>
          </div>
        </div>
      ) : (
        <div style={{ marginBottom: 10 }}>
          {item.detail && (
            <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, background: C.panel2, borderRadius: 4,
                          padding: "5px 8px", overflowWrap: "anywhere" }}>{item.detail}</div>
          )}
          {item.reason && (
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 5 }}>why: {brief(item.reason, 200)}</div>
          )}
        </div>
      )}
      <div className="flex items-center justify-between flex-wrap" style={{ gap: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
          agent {item.node_id} · {source(item.scenario)}
        </span>
        <span className="flex gap-2 items-center">
          <StatusPick item={item} />
          {item.kind === "made_up" && (
            <button onClick={() => navigate(`catch/${encodeURIComponent(item.run_id)}`)} style={small}>details</button>
          )}
          <button onClick={toStep}
            style={{ ...small, display: "flex", alignItems: "center", gap: 5, border: `1px solid ${C.steel}`, color: C.steel }}>
            <Play size={11} /> {item.kind === "dangerous" ? "where it came from" : "replay it"}
          </button>
        </span>
      </div>
    </div>
  );
}
