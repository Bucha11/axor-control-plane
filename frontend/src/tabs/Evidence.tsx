// Evidence — every EvidenceCase this deployment has caught, newest first.
//
// The EvidenceCase is the product's artifact: a reproducible discrepancy
// between what a tool really returned and what the agent then claimed. Before
// this screen there was nowhere to see them together — each lived inside the
// run that produced it, reachable only if you knew the run id. Now the loop
// has a start and an inventory: catch produces these, explain opens one,
// prevent pins them.
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight, Play, Zap } from "lucide-react";
import { api, EvidenceCaseDto, RunSummary } from "../api";
import { navigate } from "../router";
import { isConnected, useApp } from "../store";
import { C, MONO } from "../theme";

interface Row {
  run: RunSummary;
  ev: EvidenceCaseDto;
  index: number;
}

/** One line of whatever the agent claimed or the tool returned. */
export function brief(value: unknown, max = 90): string {
  if (value === null || value === undefined || value === "") return "—";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function when(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export default function Evidence() {
  const { mode } = useApp((s) => s.connection);
  const connect = useApp((s) => s.connect);
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns });

  const rows: Row[] = (runs.data ?? []).flatMap((run) =>
    (run.evidence ?? []).map((ev, index) => ({ run, ev, index })),
  );

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 700, margin: "0 0 6px" }}>Caught lies</h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 22, lineHeight: 1.6 }}>
        every time an agent claimed something its tools never gave it — what the tool
        really returned, next to what the agent said. replay any one to see how it happened.
      </div>

      {runs.isPending && (
        <div style={{ fontFamily: MONO, fontSize: 12, color: C.dim }}>loading…</div>
      )}
      {runs.isError && (
        <div style={{ fontFamily: MONO, fontSize: 12, color: C.red }}>
          could not load runs: {(runs.error as Error).message}
        </div>
      )}

      {runs.isSuccess && rows.length === 0 && (
        <div
          data-testid="evidence-empty"
          style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, padding: 20 }}
        >
          <div style={{ fontFamily: MONO, fontSize: 13, color: C.text, marginBottom: 6 }}>
            Nothing caught yet.
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, lineHeight: 1.6, marginBottom: 16 }}>
            an EvidenceCase appears here the first time an agent claims something its tools
            did not give it. the fastest way to see one is our own agent on our broken tools.
          </div>
          <div className="flex gap-2 flex-wrap">
            <button
              onClick={() => {
                connect("demo");
                navigate("catch", { auto: "1" });
              }}
              style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.green}`, borderRadius: 6, color: C.green, fontFamily: MONO, fontSize: 12, padding: "7px 14px", cursor: "pointer" }}
            >
              <Zap size={13} /> catch one in demo-mode
            </button>
            {!isConnected(mode) && (
              <button
                onClick={() => navigate("connect")}
                style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.steel}`, borderRadius: 6, color: C.steel, fontFamily: MONO, fontSize: 12, padding: "7px 14px", cursor: "pointer" }}
              >
                connect your agent <ArrowRight size={13} />
              </button>
            )}
          </div>
        </div>
      )}

      {rows.length > 0 && (
        <div className="flex flex-col gap-2" data-testid="evidence-list">
          {rows.map(({ run, ev, index }) => {
            const fault = ev.fault_attribution?.[0];
            return (
              <div
                key={`${run.run_id}:${index}`}
                data-testid="evidence-row"
                style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, padding: 14 }}
              >
                <div className="flex items-center justify-between flex-wrap" style={{ gap: 8, marginBottom: 8 }}>
                  <span className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11.5, fontWeight: 700, color: C.red }}>
                    <AlertTriangle size={13} />
                    {(ev.deviation ?? "discrepancy").replace(/_/g, " ").toLowerCase()}
                  </span>
                  <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
                    {when(run.created_ts)}
                  </span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)", gap: 10, marginBottom: 10 }}>
                  <div>
                    <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 4 }}>
                      THE TOOL {fault ? `· ${fault.tool_name}` : ""}
                    </div>
                    <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, wordBreak: "break-word" }}>
                      {fault ? `${fault.fault_mode}` : brief(ev.observed_reality)}
                    </div>
                  </div>
                  <div>
                    <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 4 }}>
                      THE AGENT SAID
                    </div>
                    <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, wordBreak: "break-word" }}>
                      {brief(ev.agent_claim)}
                    </div>
                  </div>
                </div>
                <div className="flex items-center justify-between flex-wrap" style={{ gap: 8 }}>
                  <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
                    {ev.scenario || run.scenario} · {run.node_id} · run {run.run_id}
                  </span>
                  <span className="flex gap-2">
                    <button
                      onClick={() => navigate(`catch/${encodeURIComponent(run.run_id)}`)}
                      style={{ background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut, fontFamily: MONO, fontSize: 11, padding: "4px 10px", cursor: "pointer" }}
                    >
                      receipt
                    </button>
                    <button
                      onClick={() => navigate(`explain/${encodeURIComponent(run.run_id)}`)}
                      style={{ display: "flex", alignItems: "center", gap: 5, background: "none", border: `1px solid ${C.steel}`, borderRadius: 5, color: C.steel, fontFamily: MONO, fontSize: 11, padding: "4px 10px", cursor: "pointer" }}
                    >
                      <Play size={11} /> explain
                    </button>
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
