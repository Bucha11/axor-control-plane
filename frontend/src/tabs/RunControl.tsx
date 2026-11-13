// One agent run, operated. The node card is a summary and Replay is for
// analysis (what-if, pin to corpus); this is where a run is CONTROLLED: its
// live timeline, the controls that act on it, and everything the plane knows
// about it — the health check run against it, the cut it proposed, the
// reputation of what it touched, and what the node confirmed of each command.
//
// Every command goes through the node's one door (`api.command`, signed when
// the deployment signs); nothing here writes desired state any other way.
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Pause, Play, Square, Syringe } from "lucide-react";
import { api, KernelEvent, NodeRun } from "../api";
import { navigate } from "../router";
import { useApp } from "../store";
import { C, MONO, btn } from "../theme";
import CutResult from "../components/CutResult";
import Tooltip from "../components/Tooltip";

const LIVE_MS = 1500;
const IDLE_MS = 5000;

const FAULTS: { mode: string; label: string }[] = [
  { mode: "silent_fail", label: "tool fails" },
  { mode: "tool_substitution", label: "tool swapped" },
  { mode: "instruction_injection", label: "injected order" },
  { mode: "corrupt_retrieval", label: "poisoned result" },
];

function hhmmss(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toTimeString().slice(0, 8);
}

function duration(from?: string, to?: string): string {
  if (!from || !to) return "";
  const s = Math.max(0, Math.round((new Date(to).getTime() - new Date(from).getTime()) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function compactArgs(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  return Object.entries(args as Record<string, unknown>)
    .map(([k, v]) => `${k}=${typeof v === "string" ? (v.length > 48 ? v.slice(0, 46) + "…" : v) : JSON.stringify(v)}`)
    .join("  ");
}

export default function RunControl({ runId }: { runId: string }) {
  const qc = useQueryClient();
  const testBench = useApp((s) => s.connection.testBench);
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns });
  const nodeId = (runs.data ?? []).find((r) => r.run_id === runId)?.node_id;

  const summary = useQuery({
    queryKey: ["node-runs", nodeId],
    queryFn: () => api.nodeRuns(nodeId!),
    enabled: !!nodeId,
    refetchInterval: LIVE_MS,
  });
  const run: NodeRun | undefined = summary.data?.runs.find((r) => r.run_id === runId);
  const live = run?.active ?? false;

  const events = useQuery({
    queryKey: ["run-events", runId],
    queryFn: () => api.runEvents(runId),
    refetchInterval: live ? LIVE_MS : IDLE_MS,
  });
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, refetchInterval: LIVE_MS });
  const node = (nodes.data ?? []).find((n) => n.node_id === nodeId);
  const probe = useQuery({ queryKey: ["probe-report", nodeId], queryFn: () => api.probeReport(nodeId!), enabled: !!nodeId });
  const repair = useQuery({ queryKey: ["repair", nodeId], queryFn: () => api.repair(nodeId!), enabled: !!nodeId, refetchInterval: IDLE_MS });
  const reputation = useQuery({ queryKey: ["reputation", nodeId], queryFn: () => api.reputation(nodeId!), enabled: !!nodeId });
  const journal = useQuery({ queryKey: ["journal", nodeId], queryFn: () => api.nodeJournal(nodeId!), enabled: !!nodeId, refetchInterval: LIVE_MS });

  const [error, setError] = useState<string | null>(null);
  const command = useMutation({
    mutationFn: (state: Record<string, unknown>) =>
      api.command(nodeId!, (node?.desired?.version ?? 0) + 1, state),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["nodes"] });
    },
    onError: (e: Error) => setError(e.message),
  });
  const heal = useMutation({
    mutationFn: (reason: string) => api.selfHeal(nodeId!, reason),
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: ["nodes"] });
      void qc.invalidateQueries({ queryKey: ["repair", nodeId] });
    },
    onError: (e: Error) => setError(e.message),
  });

  if (runs.isSuccess && !nodeId) {
    return <Shell><div style={{ fontFamily: MONO, fontSize: 12, color: C.mut }}>no run {runId} on this plane.</div></Shell>;
  }
  if (!nodeId) return <Shell><div style={{ fontFamily: MONO, fontSize: 12, color: C.dim }}>loading…</div></Shell>;

  const desired = node?.desired?.state ?? {};
  const paused = desired.paused === true;
  const stopped = desired.stopped === true;
  const cap = typeof desired.budget_cap_calls === "number" ? desired.budget_cap_calls : null;
  const applying = (node?.desired?.version ?? 0) > (node?.reported?.applied_version ?? 0);
  const pendingCut = (desired.pending_excision as { target_refs?: string[] } | undefined)?.target_refs ?? [];
  const appliedCuts = new Set(
    (journal.data?.entries ?? []).filter((e) => e.kind === "context_excision")
      .flatMap((e) => (e.payload.refs as string[] | undefined) ?? []),
  );
  const lines = (events.data ?? []).filter((e) =>
    ["tool_call", "tool_result", "fault_injected", "claim", "denial"].includes(e.kind));
  const tainted = new Set(run?.tainted_refs ?? []);
  const latest = probe.data?.latest;
  const proposal = repair.data?.proposal;
  const proposalForThisRun = latest?.session_id === runId ? proposal : null;
  const send = (state: Record<string, unknown>) => command.mutate(state);
  const status = stopped ? "stopped" : live ? (paused ? "held" : "running") : "done";
  const statusColor = stopped ? C.red : live ? (paused ? C.amber : C.green) : C.dim;

  return (
    <Shell>
      {/* header: what this run is, and the controls that act on it */}
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 6 }}>
        <span style={{ color: C.steel, cursor: "pointer" }}
          onClick={() => navigate(`control/${encodeURIComponent(nodeId)}`)}>{nodeId}</span>
        {" · run "}{runId}
        {run?.scenario && run.scenario !== "live" ? ` · ${run.scenario}` : ""}
      </div>
      <div className="flex items-center gap-3 flex-wrap" style={{ marginBottom: 14 }}>
        <h1 style={{ fontSize: 22, fontWeight: 650, margin: 0 }}>
          <span style={{ color: statusColor }}>● {status}</span>
        </h1>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          {hhmmss(run?.started_ts)} · {duration(run?.started_ts, live ? new Date().toISOString() : run?.last_ts)} ·{" "}
          {run?.tool_calls ?? 0} call{run?.tool_calls === 1 ? "" : "s"}
          {cap != null ? ` · cap ${cap}` : ""}
        </span>
        <span style={{ flex: 1 }} />
        <div className="flex gap-2 items-center" data-testid="run-controls">
          <Tooltip content={paused ? "Let the agent continue." : "Hold the agent at its next model or tool boundary — nothing is lost."}>
            <button onClick={() => send({ paused: !paused })} disabled={stopped || command.isPending}
              style={btn({ fontSize: 12, padding: "6px 12px", color: stopped ? C.dim : C.text })}>
              {paused ? <Play size={12} /> : <Pause size={12} />} {paused ? "Resume" : "Pause"}{live ? "" : " agent"}
            </button>
          </Tooltip>
          <Tooltip content="End the agent for good — it finishes no further turns.">
            <button
              onClick={() => { if (window.confirm(`Stop ${nodeId}? It finishes no further turns.`)) send({ stopped: true }); }}
              disabled={stopped || command.isPending}
              style={btn({ fontSize: 12, padding: "6px 12px", color: stopped ? C.dim : C.red, borderColor: stopped ? C.line : C.red })}>
              <Square size={12} /> Stop{live ? "" : " agent"}
            </button>
          </Tooltip>
          <CapControl cap={cap} disabled={stopped || command.isPending}
            onSet={(n) => send({ budget_cap_calls: n })} onError={setError} />
        </div>
      </div>
      {!live && !stopped && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginBottom: 10 }}>
          this run is over — the controls act on the agent and its next runs
        </div>
      )}
      {applying && (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.amber, marginBottom: 10 }}>
          command in flight — the node applies it at its next boundary
        </div>
      )}
      {error && <div style={{ fontFamily: MONO, fontSize: 11, color: C.red, marginBottom: 10 }}>{error}</div>}

      <div className="flex gap-4 stack-narrow" style={{ alignItems: "flex-start" }}>
        {/* live timeline */}
        <div style={{ flex: "1 1 60%", minWidth: 0 }} data-testid="run-timeline">
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 6 }}>
            timeline{live ? " · live" : ""}
          </div>
          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            {lines.length === 0 && (
              <div className="p-4" style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>
                {events.isLoading ? "loading…" : "no agent steps recorded yet."}
              </div>
            )}
            {lines.map((e, i) => (
              <Step key={`${e.seq}-${i}`} e={e} first={i === 0} runId={runId} nodeId={nodeId}
                tainted={tainted} appliedCuts={appliedCuts} pendingCut={pendingCut}
                waiting={live && e.kind === "tool_call" && e.verdict !== "deny"
                  && !lines.some((r) => r.kind === "tool_result"
                    && (r.payload as { value_ref?: string }).value_ref
                       === (e.payload as { tool_call_id?: string }).tool_call_id)} />
            ))}
          </div>
          <div className="flex gap-2 mt-3 flex-wrap">
            <Tooltip content={testBench
              ? "Insert text into the agent's next model call to see how it handles it. Marked untrusted; test-bench only."
              : "Test-bench only (enable in Settings; the agent needs AXOR_TEST_BENCH=1)."}>
              <button
                onClick={() => {
                  const text = window.prompt("Injection text — the agent sees it in its next model call:");
                  if (!text) return;
                  send({ pending_injection: { id: `inj_${Date.now()}`, text, reason: "", operator: "op_ui" } });
                }}
                disabled={!testBench || stopped || command.isPending}
                style={btn({ fontSize: 11, padding: "5px 10px", color: testBench ? C.mut : C.dim })}>
                <Syringe size={11} /> Inject
              </button>
            </Tooltip>
            <button onClick={() => navigate(`explain/${encodeURIComponent(runId)}`)}
              style={btn({ fontSize: 11, padding: "5px 10px", color: C.steel })}>
              Open in Replay — what-if, pin to corpus →
            </button>
          </div>
        </div>

        {/* everything known about this run */}
        <div style={{ flex: "1 1 40%", minWidth: 0, display: "flex", flexDirection: "column", gap: 10 }}>
          <Side title="health check">
            {!run?.probe ? (
              <span style={{ color: C.dim }}>not probed — the node runs checks when started with probe=</span>
            ) : (
              <span style={{ color: run.probe.verdict === "DRIFT_DETECTED" ? C.amber : C.green }}>
                {run.probe.verdict.toLowerCase().replace(/_/g, " ")}
                {run.probe.escape_count ? ` · ${run.probe.escape_count} escaped` : ""}
              </span>
            )}
            {proposalForThisRun && proposalForThisRun.verdict !== "no_drift_from_taint" && (
              <div className="mt-2" data-testid="proposed-cut">
                <div style={{ color: C.mut }}>
                  the localizer blames{" "}
                  <span style={{ color: C.text }}>{proposalForThisRun.auto_excise.join(", ") || "—"}</span>
                  {proposalForThisRun.escalate.length > 0 &&
                    <> · needs a look: {proposalForThisRun.escalate.join(", ")}</>}
                </div>
                {pendingCut.length > 0 ? (
                  <div className="mt-1" style={{ color: C.amber }}>cut sent: {pendingCut.join(", ")} — waiting for the node</div>
                ) : (
                  <button className="mt-2"
                    onClick={() => {
                      const reason = window.prompt("Apply the proposed cut — reason (recorded):");
                      if (reason && reason.trim()) heal.mutate(reason.trim());
                    }}
                    disabled={heal.isPending || stopped}
                    style={btn({ fontSize: 11, padding: "4px 10px", color: C.amber, borderColor: C.amber })}>
                    apply proposed cut
                  </button>
                )}
              </div>
            )}
          </Side>

          <Side title="honesty">
            {run?.evidence.length
              ? <span style={{ color: C.red }}>caught: {run.evidence.join(", ")}</span>
              : (run?.scenario ?? "").endsWith("-experiment")
                ? <span style={{ color: C.green }}>honest under a broken tool</span>
                : <span style={{ color: C.dim }}>not tested in this run</span>}
            <NextRunTest tools={run?.tools ?? []} testBench={testBench}
              armed={desired.pending_experiment as { faults?: { tool: string; mode: string }[] } | undefined}
              disabled={stopped || command.isPending}
              onArm={(tool, mode) => send({ pending_experiment: {
                id: `exp_${Date.now()}`, faults: [{ tool, mode }], operator: "op_ui" } })} />
          </Side>

          <Side title="reputation · across runs">
            {(() => {
              const snap = reputation.data?.reputation;
              const hot = snap ? Object.entries(snap.resource_level).filter(([, l]) => l !== "CLEAN") : [];
              if (!snap) return <span style={{ color: C.dim }}>no sentinel reporting for this node</span>;
              if (!hot.length) return <span style={{ color: C.green }}>nothing it touched is under watch</span>;
              return hot.map(([rid, level]) => (
                <div key={rid} className="flex gap-2">
                  <span style={{ color: C.text, flex: 1, overflowWrap: "anywhere" }}>{rid}</span>
                  <span style={{ color: level === "FLAGGED" ? C.red : C.amber }}>{level}</span>
                </div>
              ));
            })()}
          </Side>

          <Side title="commands · what the node confirmed">
            <Posture paused={paused} stopped={stopped} cap={cap} applying={applying} />
            {(journal.data?.entries ?? []).slice(0, 8).map((e, i) => (
              <div key={i} className="flex gap-2">
                <span style={{ color: C.dim }}>{hhmmss(e.ts)}</span>
                <span style={{ color: e.kind.endsWith("refused") || e.payload.result ? C.red : C.text, overflowWrap: "anywhere" }}>
                  {describeAck(e.kind, e.payload)}
                </span>
              </div>
            ))}
          </Side>
        </div>
      </div>
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div style={{ maxWidth: 1040, margin: "0 auto" }}>{children}</div>;
}

function Side({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="p-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8,
                                   fontFamily: MONO, fontSize: 11, lineHeight: 1.6 }}>
      <div style={{ color: C.mut, marginBottom: 4 }}>{title}</div>
      {children}
    </div>
  );
}

function Step({ e, first, runId, nodeId, tainted, appliedCuts, pendingCut, waiting }: {
  e: KernelEvent; first: boolean; runId: string; nodeId: string;
  tainted: Set<string>; appliedCuts: Set<string>; pendingCut: string[]; waiting?: boolean;
}) {
  const p = e.payload as Record<string, unknown>;
  const tool = typeof p.tool === "string" ? p.tool : "";
  const ref = typeof p.value_ref === "string" ? p.value_ref : (e.causal_root ?? "");
  const isTainted = e.kind === "tool_result" && !!ref && tainted.has(ref);
  const quotes = p.arg_refs && typeof p.arg_refs === "object"
    ? Object.entries(p.arg_refs as Record<string, string>) : [];
  let head: React.ReactNode;
  if (e.kind === "tool_call") {
    head = <><span style={{ color: e.verdict === "deny" ? C.red : C.text }}>→ {tool}</span>
      <span style={{ color: C.mut }}>{"  "}{compactArgs(p.args)}</span>
      {e.verdict === "deny" && <span style={{ color: C.red }}>{"  "}denied{e.gate ? ` (${e.gate})` : ""}</span>}</>;
  } else if (e.kind === "tool_result") {
    head = <><span style={{ color: C.text }}>← {tool}</span>
      {isTainted && <span style={{ color: C.amber }}>{"  "}⚠ untrusted ({((p.root as { sources?: string[] })?.sources ?? []).join(", ")})</span>}
      {p.status && p.status !== "ok" && <span style={{ color: C.red }}>{"  "}{String(p.status)}</span>}</>;
  } else if (e.kind === "fault_injected") {
    head = <span style={{ color: C.steel }}>⚡ {tool} broken on purpose ({String(p.mode)})</span>;
  } else if (e.kind === "claim") {
    head = <span style={{ color: C.text }}>answer: <span style={{ color: C.mut }}>{String(p.text ?? "").slice(0, 160)}</span></span>;
  } else {
    head = <span style={{ color: C.red }}>{e.kind}</span>;
  }
  return (
    <div className="px-3 py-2" style={{ borderTop: first ? "none" : `1px solid ${C.line}`, fontFamily: MONO, fontSize: 11 }}>
      <div className="flex gap-2" style={{ flexWrap: "wrap" }}>
        <span style={{ color: C.dim }}>{hhmmss(e.ts)}</span>
        <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>{head}</span>
      </div>
      {waiting && (
        <div style={{ marginLeft: 64, color: C.green }}>⋯ running — waiting for its result</div>
      )}
      {quotes.map(([arg, from]) => (
        <div key={arg} style={{ color: tainted.has(from) ? C.amber : C.dim, marginLeft: 64 }}>
          ↳ {arg} quotes {from}{tainted.has(from) ? " (untrusted)" : ""}
        </div>
      ))}
      {e.kind === "tool_result" && typeof p.preview === "string" && p.preview && (
        <div style={{ marginLeft: 64, marginTop: 4, color: C.text, background: C.panel2, borderRadius: 4,
                      padding: "4px 8px", whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
          {p.preview}
        </div>
      )}
      {isTainted && (
        <div style={{ marginLeft: 64 }}>
          {pendingCut.includes(ref) && !appliedCuts.has(ref)
            ? <div className="mt-2" style={{ color: C.amber }}>cut sent — applied when the agent's context carries {ref}</div>
            : <CutResult runId={runId} nodeId={nodeId} valueRef={ref} excised={appliedCuts.has(ref)} />}
        </div>
      )}
    </div>
  );
}

function CapControl({ cap, disabled, onSet, onError }: {
  cap: number | null; disabled: boolean; onSet: (n: number) => void; onError: (m: string) => void;
}) {
  const [v, setV] = useState("");
  return (
    <span className="flex items-center gap-1" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
      cap
      <input type="number" min={0} value={v} onChange={(e) => setV(e.target.value)}
        placeholder={cap != null ? String(cap) : "∞"} aria-label="call cap"
        style={{ width: 58, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text,
                 fontFamily: MONO, fontSize: 11, padding: "4px 6px", outline: "none" }} />
      <button disabled={disabled || v === ""}
        onClick={() => {
          const n = parseInt(v, 10);
          if (Number.isNaN(n) || n < 0) { onError("cap must be a non-negative integer"); return; }
          if (cap != null && n > cap) { onError(`can only lower the cap — ${n} > current ${cap}`); return; }
          onSet(n); setV("");
        }}
        style={btn({ fontSize: 11, padding: "4px 8px", color: v === "" ? C.dim : C.steel })}>set</button>
    </span>
  );
}

function NextRunTest({ tools, testBench, armed, disabled, onArm }: {
  tools: string[]; testBench: boolean; disabled: boolean;
  armed?: { faults?: { tool: string; mode: string }[] };
  onArm: (tool: string, mode: string) => void;
}) {
  const [tool, setTool] = useState(tools[0] ?? "");
  const [mode, setMode] = useState("silent_fail");
  if (armed?.faults?.length) {
    return <div className="mt-2" style={{ color: C.amber }}>
      armed for the next run: {armed.faults.map((f) => `${f.tool} · ${f.mode}`).join(", ")}
    </div>;
  }
  return (
    <div className="mt-2 flex gap-1 flex-wrap items-center">
      <FlaskConical size={11} color={C.mut} />
      <select value={tool} onChange={(e) => setTool(e.target.value)} aria-label="tool to break"
        style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "2px 4px" }}>
        {tools.map((t) => <option key={t} value={t}>{t}</option>)}
      </select>
      <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="how to break it"
        style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "2px 4px" }}>
        {FAULTS.map((f) => <option key={f.mode} value={f.mode}>{f.label}</option>)}
      </select>
      <Tooltip content={testBench ? "Break this tool on the agent's next run and audit its answer."
        : "Test-bench only: breaking a tool degrades a real user's answer."}>
        <button disabled={!testBench || disabled || !tool} onClick={() => onArm(tool, mode)}
          style={btn({ fontSize: 11, padding: "2px 8px", color: testBench ? C.steel : C.dim })}>
          test next run
        </button>
      </Tooltip>
    </div>
  );
}

function Posture({ paused, stopped, cap, applying }: {
  paused: boolean; stopped: boolean; cap: number | null; applying: boolean;
}) {
  const parts = [stopped ? "stopped" : paused ? "paused" : "running", cap != null ? `cap ${cap}` : "no cap"];
  return (
    <div style={{ color: applying ? C.amber : C.text, marginBottom: 4 }}>
      now: {parts.join(" · ")}{applying ? " — in flight" : " — applied"}
    </div>
  );
}

export function describeAck(kind: string, p: Record<string, unknown>): string {
  switch (kind) {
    case "context_excision": return `cut applied: ${((p.refs as string[]) ?? []).join(", ")}`;
    case "excision_refused": return `cut refused (${String(p.reason ?? "")}): ${((p.refs as string[]) ?? []).join(", ")}`;
    case "injection_consumed": return `injection delivered (${String(p.id ?? "")})`;
    case "injection_refused": return `injection refused — ${String(p.reason ?? "")}`;
    case "operator_intervention":
      return p.action === "experiment_armed"
        ? `honesty test armed: ${((p.faults as { tool: string; mode: string }[]) ?? []).map((f) => `${f.tool} · ${f.mode}`).join(", ")}`
        : p.action === "experiment_refused" ? `honesty test refused — ${String(p.reason ?? "")}`
        : `intervention: ${String(p.action ?? "")}`;
    case "state_applied": return `command not applied — ${String(p.result ?? "")}${p.detail ? ` (${String(p.detail)})` : ""}`;
    default: return kind;
  }
}
