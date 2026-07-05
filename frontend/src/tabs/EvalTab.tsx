// The receipt is the screen (spec section 8, main-tabs mockup): the hero is
// the caught discrepancy, never a score. Wired to /v1/runs + /v1/runs/{id}/events.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Circle } from "lucide-react";
import { api, EvidenceCaseDto, KernelEvent, RunSummary } from "../api";
import { C, MONO, sevColor } from "../theme";

function Fold({ label, count, children }: {
  label: string; count?: number; children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button onClick={() => setOpen(!open)} className="flex items-center gap-1.5 py-2"
        style={{ background: "none", border: "none", color: C.mut, fontSize: 12, fontFamily: MONO, cursor: "pointer" }}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {label}
        {count != null ? ` (${count})` : ""}
      </button>
      {open && <div className="pb-2">{children}</div>}
    </div>
  );
}

function eventLabel(e: KernelEvent): string {
  const p = e.payload as Record<string, string | number | boolean | null>;
  switch (e.kind) {
    case "tool_call":
      return `${p.tool} ${p.path ? `/${p.path}` : ""} — ${e.verdict === "deny" ? "DENIED" : "call"}`;
    case "tool_result":
      return `${p.tool} → ${p.faulted ? "faulted result" : `status ${p.status}`}`;
    case "fault_injected":
      return `⚡ injected fault: ${p.mode} on ${p.tool}`;
    case "claim":
      return `claim: "${String(p.text ?? "").slice(0, 80)}"`;
    case "denial":
      return `DENIED — ${p.reason}`;
    case "fact":
      return `fact: ${p.fact_type} (severity ${p.severity})`;
    case "context_excision":
      return `context excision`;
    default:
      return e.kind;
  }
}

function deviationHeadline(c: EvidenceCaseDto): string {
  switch (c.deviation) {
    case "fabricated_tool_result": return "fabricated a tool result";
    case "corrupted_retrieval_used": return "surfaced poisoned retrieval";
    case "direct_policy_violation": return "executed an injected instruction";
    case "undisclosed_tool_substitution": return "hid a tool substitution";
    case "budget_misreport": return "misreported its budget";
    default: return c.deviation ?? "deviated";
  }
}

export default function EvalTab() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns });
  const run: RunSummary | undefined =
    runs.data?.find((r) => r.evidence.some((c) => c.deviation)) ?? runs.data?.[0];
  const events = useQuery({
    queryKey: ["events", run?.run_id],
    queryFn: () => api.runEvents(run!.run_id),
    enabled: !!run,
  });

  if (runs.isLoading) {
    return <Center msg="loading runs…" />;
  }
  if (!run) {
    return (
      <Center msg="No runs yet. Point your agent at the proxy and run an experiment — get started lives under more…" />
    );
  }

  const caught = run.evidence.find((c) => c.deviation) ?? null;
  const denials = (events.data ?? []).filter(
    (e) => e.verdict === "deny" || e.kind === "denial",
  ).length;

  return (
    <div className="flex flex-col" style={{ maxWidth: 640, margin: "0 auto" }}>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 6 }}>
        {run.run_id} · {run.scenario}{run.intervened ? " · intervened (excluded from scores)" : ""}
      </div>
      <h1 style={{ fontSize: 22, fontWeight: 650, lineHeight: 1.3, margin: "0 0 20px" }}>
        {caught
          ? (<>Your agent <span style={{ color: C.red }}>{deviationHeadline(caught)}</span>{caught.fault_attribution[0] ? ` when ${caught.fault_attribution[0].tool_name} was deprived.` : "."}</>)
          : run.completed
            ? (<>No discrepancies caught in this run.</>)
            : (<>Run in progress.</>)}
      </h1>

      {caught && (
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, overflow: "hidden" }}>
          <div className="p-4" style={{ borderBottom: `1px solid ${C.line}` }}>
            <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>WHAT HAPPENED</div>
            <div style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>
              {JSON.stringify(caught.observed_reality)}
            </div>
          </div>
          <div className="p-4">
            <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>WHAT THE AGENT SAID</div>
            <div style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>
              {JSON.stringify(caught.agent_claim)}
            </div>
          </div>
          <div className="px-4 py-3 flex items-center justify-between" style={{ background: "rgba(229,72,77,0.06)", borderTop: `1px solid ${C.line}` }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.red, fontWeight: 700 }}>
              {(caught.deviation ?? "").toUpperCase().replaceAll("_", " ")} · {caught.verdict_source} · confidence {caught.confidence}
            </span>
          </div>
        </div>
      )}

      <Fold label="full trace" count={events.data?.length}>
        {(events.data ?? []).map((e) => (
          <div key={e.seq} className="flex items-center gap-2 py-1">
            <Circle size={7} fill={sevColor(e.verdict, e.kind)} color={sevColor(e.verdict, e.kind)} />
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: e.verdict === "deny" || e.kind === "fault_injected" ? C.text : C.mut }}>
              {eventLabel(e)}
            </span>
          </div>
        ))}
      </Fold>
      <Fold label={`denials — ${denials}`}>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, paddingLeft: 18 }}>
          {denials === 0 ? "none in this run" : `${denials} denial-bearing steps; open replay for the gate-by-gate view`}
        </div>
      </Fold>
    </div>
  );
}

function Center({ msg }: { msg: string }) {
  return (
    <div style={{ maxWidth: 640, margin: "0 auto", fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
      {msg}
    </div>
  );
}
