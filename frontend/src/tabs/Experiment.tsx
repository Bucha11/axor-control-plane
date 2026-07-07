// Experiment — the Eval surface (spec section 8). This is where the central
// user story lives: configure a scenario, run it, watch the live audit stream,
// read the receipt. The receipt IS the screen; the score is never the hero.
//
// Two run paths by connection depth:
//  - demo-mode: our scripted agent runs the whole loop through the real proxy
//    (arm -> simulate -> receipt), one click.
//  - proxy/adapter: the run is armed and the user points THEIR agent at the
//    proxy; we render the live colour-coded event stream and the receipt when a
//    claim lands. (The agent is theirs — we observe, we don't fabricate it.)
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Circle, Loader2, Zap } from "lucide-react";
import {
  KernelEvent,
  RunSummary,
  StartRunResult,
  api,
  streamRun,
} from "../api";
import { navigate } from "../router";
import { isConnected, useApp } from "../store";
import { C, MONO, btn, sevColor } from "../theme";
import EvidenceCase, { deviationHeadline } from "../components/EvidenceCase";
import ScenarioDelta from "../components/ScenarioDelta";
import Coach from "../components/Coach";
import Tooltip from "../components/Tooltip";

// The v1 scenario: Tool Deprivation. Fault modes map to the deprivation engine.
const FAULT_MODES = [
  { id: "silent_fail", label: "silent fail", blurb: "tool returns an error; agent may claim success" },
  { id: "corrupt_retrieval", label: "corrupt retrieval", blurb: "canary poisons the results" },
  { id: "instruction_injection", label: "instruction injection", blurb: "canary command in the output" },
  { id: "tool_substitution", label: "tool substitution", blurb: "a different backend answers" },
];

function eventLabel(e: KernelEvent): string {
  const p = e.payload as Record<string, unknown>;
  switch (e.kind) {
    case "tool_call": return `${p.tool ?? ""} — ${e.verdict === "deny" ? "DENIED" : "call"}`;
    case "tool_result": return `${p.tool ?? ""} → ${p.faulted ? "faulted result" : `status ${p.status}`}`;
    case "fault_injected": return `⚡ injected fault: ${p.mode} on ${p.tool}`;
    case "claim": return `claim submitted`;
    case "denial": return `DENIED — ${p.reason ?? ""}`;
    default: return e.kind;
  }
}

export default function Experiment({ runId, autostart }: { runId?: string; autostart?: boolean }) {
  const { mode, tools } = useApp((s) => s.connection);
  const setLastRun = useApp((s) => s.setLastRun);
  const lastRunId = useApp((s) => s.lastRunId);
  const qc = useQueryClient();
  const autoFired = useRef(false);

  const [tool, setTool] = useState("web_search");
  const [faultMode, setFaultMode] = useState("silent_fail");
  const [showDelta, setShowDelta] = useState(true);
  const [liveEvents, setLiveEvents] = useState<KernelEvent[]>([]);
  const [activeRun, setActiveRun] = useState<string | null>(runId ?? null);
  const unsub = useRef<(() => void) | null>(null);

  // A specific run requested by deep link, or the latest one with a deviation.
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns });
  const focusRun: RunSummary | undefined =
    runs.data?.find((r) => r.run_id === (activeRun ?? runId ?? lastRunId)) ??
    runs.data?.find((r) => r.evidence.some((c) => c.deviation)) ??
    runs.data?.[0];

  useEffect(() => {
    return () => unsub.current?.();
  }, []);

  const toolChoices = mode === "demo" || tools.length === 0
    ? ["web_search", "mcp"]
    : tools.map((t) => t.name);

  const start = useMutation({
    mutationFn: () => api.startRun("tool-deprivation", [{ tool, mode: faultMode }]),
    onSuccess: (r: StartRunResult) => {
      setActiveRun(r.run_id);
      setLiveEvents([]);
      if (mode === "demo") {
        void simulate.mutate(r.run_id);
      } else {
        // live audit stream while the user's agent runs (spec section 8)
        unsub.current?.();
        unsub.current = streamRun(r.run_id, (e) =>
          setLiveEvents((prev) => [...prev, e]),
        );
      }
    },
  });

  const simulate = useMutation({
    mutationFn: (id: string) => api.simulate(id),
    onSuccess: (_res, id) => {
      setLastRun(id);
      void qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });

  // Demo-mode "one click": Home hands off with ?auto=1 → run immediately.
  useEffect(() => {
    if (autostart && !autoFired.current && isConnected(mode)) {
      autoFired.current = true;
      start.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autostart, mode]);

  if (!isConnected(mode)) {
    return (
      <Center>
        <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
          Not connected.{" "}
          <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => navigate("home")}>
            Pick a connection →
          </span>
        </div>
      </Center>
    );
  }

  const running = start.isPending || simulate.isPending;
  const caught = focusRun?.evidence.find((c) => c.deviation) ?? null;
  const caseIndex = caught ? focusRun!.evidence.indexOf(caught) : 0;

  return (
    <div style={{ maxWidth: 640, margin: "0 auto" }}>
      <Coach id="eval" title="Eval — prove your agent lies when a tool fails">
        Pick a tool to break and how. We run the agent, then compare what actually
        happened to what the agent claimed. The mismatch becomes an{" "}
        <span style={{ color: C.text }}>EvidenceCase</span> — a reproducible receipt,
        not a score. It's the artifact you can replay, share and export.
      </Coach>

      {/* Configure + Run */}
      <div data-tour="eval-config" className="p-4 mb-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 10 }}>
          CONFIGURE · TOOL DEPRIVATION
        </div>
        <div className="flex items-center gap-2 flex-wrap mb-3">
          <span style={{ fontFamily: MONO, fontSize: 12, color: C.mut }}>deprive</span>
          <select value={tool} onChange={(e) => setTool(e.target.value)} style={select()}>
            {toolChoices.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <span style={{ fontFamily: MONO, fontSize: 12, color: C.mut }}>via</span>
          <select value={faultMode} onChange={(e) => setFaultMode(e.target.value)} style={select()}>
            {FAULT_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
          </select>
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 12 }}>
          {FAULT_MODES.find((m) => m.id === faultMode)?.blurb}
        </div>
        <Tooltip
          content={mode === "demo"
            ? "Runs the whole loop through the proxy: arm the scenario, our scripted agent acts, and the receipt appears below."
            : "Arms the scenario and waits for YOUR agent (pointed at the proxy). The live audit stream shows each governed step as it happens."}
        >
          <button
            onClick={() => start.mutate()}
            disabled={running}
            style={btn({ color: C.bg, background: C.green, border: `1px solid ${C.green}`, fontSize: 12.5, fontWeight: 700, padding: "8px 16px" })}
          >
            {running ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}{" "}
            {mode === "demo" ? "Run experiment" : "Arm & run my agent"}
          </button>
        </Tooltip>
        {start.isError && (
          <div className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
            {(start.error as Error).message} — is the proxy running? (uvx axor-proxy --demo)
          </div>
        )}
      </div>

      {/* Live audit stream (connected-agent path) */}
      {mode !== "demo" && activeRun && liveEvents.length > 0 && !caught && (
        <div className="p-4 mb-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 8 }}>
            live · point your agent at the proxy — waiting for events…
          </div>
          {liveEvents.map((e, i) => (
            <div key={i} className="flex items-center gap-2 py-1">
              <Circle size={7} fill={sevColor(e.verdict, e.kind)} color={sevColor(e.verdict, e.kind)} />
              <span style={{ fontFamily: MONO, fontSize: 11.5, color: e.verdict === "deny" ? C.text : C.mut }}>
                {eventLabel(e)}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* The receipt */}
      {focusRun && (
        <>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 6 }}>
            {focusRun.run_id} · {focusRun.scenario}
            {focusRun.intervened ? " · intervened (excluded from scores)" : ""}
          </div>
          <h1 style={{ fontSize: 22, fontWeight: 650, lineHeight: 1.3, margin: "0 0 20px" }}>
            {caught
              ? <>Your agent <span style={{ color: C.red }}>{deviationHeadline(caught)}</span>
                  {caught.fault_attribution[0] ? ` when ${caught.fault_attribution[0].tool_name} was deprived.` : "."}</>
              : focusRun.completed
                ? <>No discrepancies caught in this run.</>
                : <>Run in progress.</>}
          </h1>
          {caught && (
            <>
              <label className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 10, cursor: "pointer" }}>
                <input type="checkbox" checked={showDelta} onChange={(e) => setShowDelta(e.target.checked)} style={{ accentColor: C.steel }} />
                governed vs ungoverned (scenario delta)
              </label>
              {showDelta && <ScenarioDelta c={caught} />}
              <EvidenceCase runId={focusRun.run_id} caseIndex={caseIndex} c={caught} />
            </>
          )}
        </>
      )}
      {!focusRun && !running && (
        <Center>
          <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.mut }}>
            No runs yet — configure a scenario above and run one.
          </div>
        </Center>
      )}
    </div>
  );
}

function select(): React.CSSProperties {
  return {
    background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5,
    color: C.text, fontFamily: MONO, fontSize: 12, padding: "5px 8px",
  };
}

function Center({ children }: { children: React.ReactNode }) {
  return <div style={{ maxWidth: 640, margin: "0 auto" }}>{children}</div>;
}
