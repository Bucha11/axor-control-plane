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
import { refusalWords } from "../refusal";
import { api, EvidenceCaseDto, IncidentItem, IncidentKind, IncidentStatus } from "../api";
import { navigate } from "../router";
import { isConnected, useApp } from "../store";
import { C, MONO } from "../theme";
import { useCanAdmin } from "../team";
import { errorWords } from "../refusal";
import { agentLabel } from "../agentName";
import { useWorkStarted } from "../workStarted";


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
  // seen in live work, not broken on purpose
  observed_error: "failed — its answer was an error",
  observed_held: "never ran — the operator had held the agent",
};
const toolDid = (mode: string) => TOOL_DID[mode] ?? mode.replace(/_/g, " ");

// where an incident came from: the plane's own examples, a test run, or work
const EXAMPLES = new Set(["adapter-demo", "multi-agent-demo", "coding-demo", "skill-demo", "ops-demo"]);
const TESTS = new Set(["tool-deprivation", "exfil", "injection"]);
function source(scenario?: string, brokenOnPurpose = false): string {
  if (scenario && EXAMPLES.has(scenario)) return "example data";
  // a wrapped agent's test bench breaks a tool inside its live run
  if ((scenario && TESTS.has(scenario)) || brokenOnPurpose) return "a chaos test";
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
  ["made_up,dangerous,would_refuse,refused,memory_worse", "needs a look"],
  ["made_up", "made something up"],
  ["dangerous", "dangerous, let through"],
  ["would_refuse", "would have been refused"],
  ["refused", "refused — stopped"],
  ["memory_worse", "worse after a memory change"],
  ["", "everything"],
];
const STATUS_WORDS: Record<IncidentStatus, string> = {
  new: "new", looked_at: "looked at", kept_as_test: "kept as a test",
  rule_made: "made a rule", not_a_problem: "not a problem",
};
// who asked for a refused call
const ORIGIN_WORDS: Record<string, string> = {
  web_page: "a web page asked for it", outside: "outside text asked for it",
  skill: "a skill asked for it", agent: "the agent's own call",
};
const KIND_COLOR: Record<IncidentKind, string> = {
  made_up: C.red, dangerous: C.red, would_refuse: C.amber, refused: C.dim, memory_worse: C.amber,
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
  const [kind, setKind] = useState("made_up,dangerous,would_refuse,memory_worse");
  const [status, setStatus] = useState("new");
  // `?agent=` — arriving from an agent's row in Agents
  const [agent, setAgent] = useState(agentParam ?? "");
  const feed = useQuery({
    queryKey: ["incidents", kind, status, agent],
    queryFn: () => api.incidents({ kind, status, agent }),
  });
  const all = useQuery({ queryKey: ["incidents", "all"], queryFn: () => api.incidents({}) });
  const agents = [...new Set((all.data?.items ?? []).map((i) => i.node_id))].sort();
  // who and what: a coding session reads as its developer and its first request
  const nodesQ = useQuery({ queryKey: ["nodes"], queryFn: api.nodes });
  const labelOf = (id: string) => {
    const pr = (nodesQ.data ?? []).find((n) => n.node_id === id)?.profile;
    if (!pr?.title && !pr?.developer) return agentLabel(id);
    return [pr.developer, pr.title ? `“${pr.title}”` : ""].filter(Boolean).join(" · ");
  };
  const [cause, setCause] = useState("");
  const listed = feed.data?.items ?? [];
  const items = cause ? listed.filter((i) => i.kind !== "refused" || i.cause === cause) : listed;
  const developerOf = (id: string) => (nodesQ.data ?? []).find((n) => n.node_id === id)?.profile?.developer || id;
  // a coding session (or a tree run) is a developer's; anything else is a production agent
  // (a node that never said what it is counts by the side of the menu in use)
  const isSession = (id: string) => {
    const kind = (nodesQ.data ?? []).find((n) => n.node_id === id)?.profile?.kind;
    return kind ? ["coding", "tree"].includes(String(kind)) : journey === "coding";
  };
  const nothingAtAll = all.isSuccess && all.data.items.length === 0;
  const started = useWorkStarted();

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
            through, or the rules refuse one.{!started && <> the fastest way to see one: test our example agent — we
            break its search on purpose and check what it says.</>}
          </div>
          <div className="flex gap-2 flex-wrap">
            {!started && <button onClick={() => { connect("demo"); navigate("catch", { auto: "1" }); }}
              style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.green}`, borderRadius: 6, color: C.green, fontFamily: MONO, fontSize: 12, padding: "7px 14px", cursor: "pointer" }}>
              <Zap size={13} /> test the example agent
            </button>}
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
                         fontFamily: MONO, fontSize: 11, padding: "3px 6px", marginLeft: 6, maxWidth: 220 }}>
                <option value="">any agent</option>
                {agents.map((a) => <option key={a} value={a}>{labelOf(a)}</option>)}
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
              <RefusalSummary items={listed} developerOf={developerOf} isSession={isSession}
                cause={cause} setCause={setCause} />
              {items.map((i) => <Item key={i.id} item={i} who={labelOf(i.node_id)} />)}
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
      {(Object.keys(STATUS_WORDS) as IncidentStatus[])
        // a refusal already is a rule at work: keeping it or making one is not its question
        .filter((st) => item.kind !== "refused" || st === item.status || !["kept_as_test", "rule_made"].includes(st))
        .map((st) => <option key={st} value={st}>{STATUS_WORDS[st]}</option>)}
    </select>
  );
}

function Item({ item, who }: { item: IncidentItem; who?: string }) {
  if (item.kind === "memory_worse") return <MemoryWorse item={item} />;
  const color = KIND_COLOR[item.kind];
  const toStep = () => navigate(`explain/${encodeURIComponent(item.run_id)}`,
    item.seq != null ? { seq: item.seq, node: item.anchor_node ?? item.node_id } : undefined);
  // a stop held the calls: nothing failed, the agent answered as if they had run
  const held = (item.case?.fault_attribution ?? []).filter((f) => f.fault_mode === "observed_held");
  const title = item.kind === "made_up" && held.length ? "answered as if calls it was stopped from had run"
    : item.kind === "made_up" ? incidentWhat(item.what)
    : item.kind === "dangerous" ? item.what
      : item.kind === "would_refuse" ? `${item.what} — would have been refused`
        : `${item.what} — refused${item.count > 1 ? ` ×${item.count}` : ""}`;
  const c = item.case;
  const fault = c?.fault_attribution?.[0];
  const check = c ? checkOf(c) : null;
  return (
    <div data-testid={item.kind === "made_up" ? "evidence-row" : "incident-row"} data-kind={item.kind} data-cause={item.cause}
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
                : held.length > 1 ? <>THE TOOLS · {held.map((f) => f.tool_name).join(", ")}</>
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
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 5 }}>why: {brief(refusalWords(item.reason), 200)}
              {(() => {
                const meant = item.reason.split(/If you want it, run it yourself:\s*/)[1];
                const allow = item.reason.split(/if this repository needs it,?\s*|or allow skill scripts in this repository:\s*/)[1];
                if (/team's policy refuses it/.test(item.reason)) {
                  return <div style={{ color: C.dim }}>your team's policy refuses it —{" "}
                    <button onClick={() => navigate("policy")} style={{ background: "none", border: "none", padding: 0,
                      cursor: "pointer", color: C.steel, fontFamily: MONO, fontSize: 11 }}>Policy →</button></div>;
                }
                const hint = meant ? `if you meant it, run it yourself: ${meant}` : allow ? `to allow it here: ${allow}` : "";
                return hint ? <div style={{ color: C.dim }}>{hint}</div> : null;
              })()}
            </div>
          )}
        </div>
      )}
      <div className="flex items-center justify-between flex-wrap" style={{ gap: 8 }}>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
          {who && who !== item.node_id ? who : <>agent {item.node_id}</>} · {source(item.scenario, (item.case?.fault_attribution ?? [])
            .some((a) => a.fault_mode && !a.fault_mode.startsWith("observed_")))}
          {item.origin && <span data-testid="incident-origin"> · {ORIGIN_WORDS[item.origin] ?? item.origin}</span>}
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
      {item.kind === "refused" && <AllowExactly item={item} />}
      {(item.kind === "dangerous" || item.kind === "would_refuse") && <Mute item={item} />}
    </div>
  );
}

/** The refusals, counted by cause: how many, and how many developers hit it —
 *  a rule too strict shows as many refusals across the team. */
function RefusalSummary({ items, developerOf, isSession, cause, setCause }: {
  items: IncidentItem[]; developerOf: (id: string) => string; isSession: (id: string) => boolean;
  cause: string; setCause: (c: string) => void;
}) {
  const refused = items.filter((i) => i.kind === "refused" && i.cause);
  if (refused.length < 2) return null;
  const by = new Map<string, { words: string; n: number; devs: Set<string>; people: boolean }>();
  for (const i of refused) {
    const g = by.get(i.cause!) ?? { words: i.cause_words || i.cause!, n: 0, devs: new Set<string>(), people: true };
    g.n += i.count;
    g.devs.add(developerOf(i.node_id));
    // a production agent is not a developer's session: it counts as an agent
    if (!isSession(i.node_id)) g.people = false;
    by.set(i.cause!, g);
  }
  const rows = [...by.entries()].sort((a, b) => b[1].n - a[1].n);
  return (
    <div data-testid="refusal-summary" className="flex gap-1.5 flex-wrap items-center mb-1"
      style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
      <span>refused, by cause:</span>
      {rows.map(([k, g]) => (
        <button key={k} data-testid={`refusal-cause-${k}`} onClick={() => setCause(cause === k ? "" : k)} style={chip(cause === k)}>
          {g.words} · {g.n} · {g.devs.size} {g.people
            ? (g.devs.size === 1 ? "developer" : "developers")
            : (g.devs.size === 1 ? "agent" : "agents")}
        </button>
      ))}
    </div>
  );
}

const fieldSmall: React.CSSProperties = {
  background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
  fontFamily: MONO, fontSize: 11, padding: "3px 6px",
};

/** A refusal by one of the team's rules: let exactly this through, for the
 *  team (a path or a command — edit it to a glob), on the Policy. */
function AllowExactly({ item }: { item: IncidentItem }) {
  const canAdmin = useCanAdmin();
  const qc = useQueryClient();
  const rules = useQuery({ queryKey: ["coding-rules"], queryFn: api.codingRules, enabled: canAdmin, retry: false });
  const kind: "path" | "command" | null = item.target?.path ? "path" : item.target?.command ? "command" : null;
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState(kind ? item.target![kind]! : "");
  const [note, setNote] = useState("");
  const add = useMutation({
    mutationFn: () => api.addCodingException({ rule: item.cause!, [kind!]: value.trim(), note }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["coding-rules"] });
      void api.setIncidentStatus(item.id, "not_a_problem").then(() => qc.invalidateQueries({ queryKey: ["incidents"] }));
    },
  });
  const rule = rules.data?.catalog?.find((r) => r.id === item.cause && !r.fixed);
  if (!canAdmin) return null;
  if (item.origin === "web_page" && item.cause === "outside_text") {
    return (
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 8 }}>
        if that page was documentation you trust, add its host to{" "}
        <button onClick={() => navigate("policy")} style={inlineLink}>trusted sources on Policy →</button>
      </div>
    );
  }
  if (!rule || !kind) return null;
  if (add.isSuccess) {
    return <div data-testid="allow-exactly-done" style={{ fontFamily: MONO, fontSize: 11, color: C.green, marginTop: 8 }}>
      allowed for the team — every sidecar picks it up within a minute</div>;
  }
  return (
    <div style={{ marginTop: 8 }}>
      {!open ? (
        <button data-testid="allow-exactly" onClick={() => setOpen(true)} style={small}>
          allow exactly this for the team…
        </button>
      ) : (
        <div className="flex items-center gap-2 flex-wrap" style={{ fontFamily: MONO, fontSize: 11 }}>
          <span style={{ color: C.dim }}>{rule.what}, except {kind}</span>
          <input data-testid="allow-exactly-value" value={value} onChange={(e) => setValue(e.target.value)}
            style={{ ...fieldSmall, flex: 1, minWidth: 200 }} />
          <input aria-label="why" value={note} onChange={(e) => setNote(e.target.value)} placeholder="why (optional)"
            style={{ ...fieldSmall, width: 140 }} />
          <button data-testid="allow-exactly-save" disabled={!value.trim() || add.isPending} onClick={() => add.mutate()}
            style={{ ...small, border: `1px solid ${C.amber}`, color: C.amber }}>allow for the team</button>
          <button onClick={() => setOpen(false)} style={small}>cancel</button>
          <div style={{ width: "100%", color: C.dim, fontSize: 10.5 }}>
            * matches anything ({kind === "path" ? "tests/fixtures/*" : "pytest *"}); the rule keeps refusing everything else
            {rules.data && !rules.data.set ? " · this saves the team's policy" : ""}
          </div>
          {add.isError && <div style={{ width: "100%", color: C.red }}>{errorWords((add.error as Error).message)}</div>}
        </div>
      )}
    </div>
  );
}

/** "Not a problem — don't flag this again": this call (edit it to a glob)
 *  stops being an incident. */
function Mute({ item }: { item: IncidentItem }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(item.detail);
  const mute = useMutation({
    mutationFn: async () => {
      await api.muteIncident({ kind: item.kind, what: item.what, detail });
      await api.setIncidentStatus(item.id, "not_a_problem");
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["incidents"] }),
  });
  if (!item.detail) return null;
  return (
    <div style={{ marginTop: 8 }}>
      {!open ? (
        <button data-testid="incident-mute" onClick={() => setOpen(true)} style={small}>
          not a problem — don't flag this again…
        </button>
      ) : (
        <div className="flex items-center gap-2 flex-wrap" style={{ fontFamily: MONO, fontSize: 11 }}>
          <span style={{ color: C.dim }}>stop flagging</span>
          <input data-testid="incident-mute-detail" value={detail} onChange={(e) => setDetail(e.target.value)}
            style={{ ...fieldSmall, flex: 1, minWidth: 200 }} />
          <button data-testid="incident-mute-save" disabled={!detail.replace(/[*\s]/g, "") || mute.isPending}
            onClick={() => mute.mutate()} style={small}>don't flag it again</button>
          <button onClick={() => setOpen(false)} style={small}>cancel</button>
          <div style={{ width: "100%", color: C.dim, fontSize: 10.5 }}>
            * matches anything (rm -rf build/*); it still runs and is still recorded — it just stops being an incident
          </div>
          {mute.isError && <div style={{ width: "100%", color: C.red }}>{errorWords((mute.error as Error).message)}</div>}
        </div>
      )}
    </div>
  );
}


/** The team's memory changed, and the sessions after did significantly worse. */
function MemoryWorse({ item }: { item: IncidentItem }) {
  const c = item.compare;
  const pct = (s: { sessions: number; bad: number }) => `${s.bad} of ${s.sessions}`;
  return (
    <div data-testid="incident-row" data-kind="memory_worse"
      style={{ background: C.panel, border: `1px solid ${C.line}`, borderLeft: `3px solid ${C.amber}`, borderRadius: 10, padding: 14 }}>
      <div className="flex items-center justify-between flex-wrap" style={{ gap: 8, marginBottom: 8 }}>
        <span className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 700, color: C.amber }}>
          <AlertTriangle size={13} /> {item.what}
        </span>
        <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>{when(item.ts)}</span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, background: C.panel2, borderRadius: 4,
                    padding: "5px 8px", overflowWrap: "anywhere", marginBottom: 8 }}>{item.detail}</div>
      {c && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 10 }}>
          sessions that went wrong (first check failed, or said checks passed when they failed):
          before {pct(c.before)} · after {pct(c.after)}{c.p != null ? ` · p ${c.p < 0.001 ? "<0.001" : c.p.toPrecision(2)}` : ""}
        </div>
      )}
      <div className="flex items-center justify-end flex-wrap" style={{ gap: 8 }}>
        <StatusPick item={item} />
        <button onClick={() => navigate("memory")}
          style={{ ...small, border: `1px solid ${C.steel}`, color: C.steel }}>see the team memory →</button>
      </div>
    </div>
  );
}
