// Standalone demo landing (demo-landing mockup): a recorded trace replayed
// deterministically on an SVG stage — no live model, same outcome every time.
import { useState, useEffect } from "react";
import { Play, RotateCcw, ArrowRight, Shield } from "lucide-react";
import { C, MONO } from "../theme";

// Page + stage backgrounds are the mockup's own darker shades (not in the shared palette).
const PAGE_BG = "#0E1216";
const STAGE_BG = "#11151A";

// ---- fixed, deterministic scene (spec §4: recorded, reproducible every time) ----
const N = {
  user:   { x: 70,  y: 150, label: "you" },
  agent:  { x: 300, y: 150, label: "agent" },
  email:  { x: 560, y: 60,  label: "email.read" },
  search: { x: 560, y: 150, label: "web_search" },
  slack:  { x: 560, y: 240, label: "slack.post" },
} as const;
type NodeId = keyof typeof N;
const path = (a: NodeId, b: NodeId) => `M ${N[a].x} ${N[a].y} L ${N[b].x} ${N[b].y}`;

interface Step {
  dur: number;
  cap: string;
  pulse?: [NodeId, NodeId];
  color?: string;
  taintEmail?: boolean;
  searchFail?: boolean;
  claim?: boolean;
  exporting?: boolean;
  blocked?: boolean;
  receipt?: boolean;
}
type Flag = "taintEmail" | "searchFail" | "claim" | "blocked" | "receipt";

// script: recorded trace replayed on the graph — no live model, ever
const SCRIPT: Step[] = [
  { dur: 1400, pulse: ["user", "agent"], color: C.steel, cap: "task: “summarize this week and post to slack”" },
  { dur: 1400, pulse: ["agent", "email"], color: C.steel, cap: "agent reads the inbox…" },
  { dur: 1400, pulse: ["email", "agent"], color: C.amber, taintEmail: true, cap: "…external content comes back — the value is now tainted" },
  { dur: 1500, pulse: ["agent", "search"], color: C.steel, cap: "agent calls web_search…" },
  { dur: 1600, searchFail: true, cap: "web_search → ToolError(timeout). Zero bytes returned." },
  { dur: 2400, claim: true, cap: "the agent doesn’t mention it:" },
  { dur: 1700, pulse: ["agent", "slack"], color: C.red, exporting: true, cap: "…and tries to post the fabrication, tainted value included" },
  { dur: 2200, blocked: true, cap: "the gate denies at the sink: tainted value in an export projection" },
  { dur: 99999, receipt: true, cap: "" },
];

const IDLE: Step = { dur: 0, cap: "" };

export default function DemoLanding() {
  const [playing, setPlaying] = useState(false);
  const [step, setStep] = useState(-1);

  useEffect(() => {
    if (!playing || step >= SCRIPT.length - 1) return;
    const t = setTimeout(() => setStep((s) => s + 1), step < 0 ? 200 : SCRIPT[step].dur);
    return () => clearTimeout(t);
  }, [playing, step]);

  const done = (k: Flag) => step >= 0 && SCRIPT.slice(0, step + 1).some((s) => s[k]);
  const cur = step >= 0 ? SCRIPT[step] : IDLE;
  const tainted = done("taintEmail");
  const failed = done("searchFail");
  const claimed = done("claim");
  const blocked = done("blocked");
  const receipt = done("receipt");

  const Node = ({ id, danger, warn }: { id: NodeId; danger?: boolean; warn?: boolean }) => {
    const n = N[id];
    const ring = danger ? C.red : warn ? C.amber : C.line;
    return (
      <g>
        <circle cx={n.x} cy={n.y} r="22" fill={C.panel} stroke={ring} strokeWidth="1.5"
          style={danger || warn ? { filter: `drop-shadow(0 0 6px ${ring})` } : {}} />
        <text x={n.x} y={n.y + 40} textAnchor="middle" fill={C.mut} fontSize="11" fontFamily={MONO}>{n.label}</text>
      </g>
    );
  };

  return (
    <div style={{ minHeight: "100vh", background: PAGE_BG, color: C.text, fontFamily: "Inter, system-ui, sans-serif", display: "flex", flexDirection: "column", alignItems: "center", padding: "48px 20px" }}>
      <style>{`
        @keyframes fadeUp { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
        @keyframes blink { 0%,100% { opacity: 1; } 50% { opacity: 0.25; } }
      `}</style>

      <div style={{ maxWidth: 680, width: "100%" }}>
        <div style={{ fontFamily: MONO, fontSize: 12, color: C.steel, letterSpacing: "0.1em", marginBottom: 12 }}>AXOR CONTROL PLANE</div>
        <h1 style={{ fontSize: 30, fontWeight: 700, lineHeight: 1.25, margin: "0 0 8px" }}>
          Your agent lies when its tools fail.<br />
          <span style={{ color: C.mut }}>Watch one get caught.</span>
        </h1>
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 20 }}>
          a recorded trace, replayed deterministically — no live model, same outcome every time
        </div>

        {/* the stage */}
        <div style={{ background: STAGE_BG, border: `1px solid ${C.line}`, borderRadius: 10, position: "relative", overflow: "hidden" }}>
          <svg viewBox="0 0 660 300" style={{ width: "100%", display: "block" }}>
            {/* static edges */}
            {([["user", "agent"], ["agent", "email"], ["agent", "search"], ["agent", "slack"]] as [NodeId, NodeId][]).map(([a, b]) => (
              <path key={a + b} d={path(a, b)} stroke={C.line} strokeWidth="1" fill="none" />
            ))}
            {/* export edge turns red once blocked */}
            {blocked && <path d={path("agent", "slack")} stroke={C.red} strokeWidth="1.5" strokeDasharray="5 4" fill="none" />}

            {/* travelling pulse (keyed remount restarts SMIL per step) */}
            {playing && cur.pulse && !receipt && (
              <circle key={step} r="5" fill={cur.color ?? C.steel} style={{ filter: `drop-shadow(0 0 5px ${cur.color ?? C.steel})` }}>
                <animateMotion dur={`${cur.dur / 1000}s`} fill="freeze" path={path(cur.pulse[0], cur.pulse[1])} />
              </circle>
            )}

            {/* gate shield on the export edge */}
            {(blocked || receipt) && (
              <g style={{ animation: "fadeUp .4s ease both" }}>
                <circle cx="440" cy="198" r="15" fill={C.panel} stroke={C.red} strokeWidth="1.5" />
                <text x="440" y="203" textAnchor="middle" fontSize="13">🛡</text>
                <text x="440" y="232" textAnchor="middle" fill={C.red} fontSize="10" fontFamily={MONO} fontWeight="700">DENIED</text>
              </g>
            )}

            <Node id="user" />
            <Node id="agent" danger={claimed && !receipt} />
            <Node id="email" warn={tainted} />
            <Node id="search" danger={failed && step < 7} />
            <Node id="slack" />

            {/* tool failure tag */}
            {failed && !receipt && (
              <text x={N.search.x} y={N.search.y - 32} textAnchor="middle" fill={C.red} fontSize="10.5" fontFamily={MONO}
                style={step === 4 ? { animation: "blink 1s ease 2" } : {}}>ToolError(timeout)</text>
            )}
            {tainted && !receipt && (
              <text x={N.email.x} y={N.email.y - 32} textAnchor="middle" fill={C.amber} fontSize="10.5" fontFamily={MONO}>tainted</text>
            )}

            {/* the claim bubble */}
            {claimed && !receipt && (
              <g style={{ animation: "fadeUp .4s ease both" }}>
                <rect x="180" y="55" width="240" height="40" rx="6" fill={C.panel} stroke={C.red} strokeWidth="1" />
                <text x="300" y="72" textAnchor="middle" fill={C.text} fontSize="10.5" fontFamily={MONO}>“Based on the search results,</text>
                <text x="300" y="86" textAnchor="middle" fill={C.text} fontSize="10.5" fontFamily={MONO}>rates rose 0.25%”</text>
              </g>
            )}
          </svg>

          {/* caption bar */}
          <div style={{ borderTop: `1px solid ${C.line}`, padding: "10px 16px", minHeight: 40, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            {!playing ? (
              <button onClick={() => { setPlaying(true); setStep(-1); }}
                style={{ display: "flex", alignItems: "center", gap: 8, background: C.steel, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12.5, fontWeight: 700, padding: "8px 18px", cursor: "pointer" }}>
                <Play size={14} /> Run the recording
              </button>
            ) : (
              <span key={step} style={{ fontFamily: MONO, fontSize: 12, color: cur.blocked ? C.red : C.mut, animation: "fadeUp .3s ease both" }}>
                {cur.cap}
              </span>
            )}
            {playing && (
              <button onClick={() => setStep(-1)} style={{ background: "none", border: "none", color: C.dim, cursor: "pointer" }}>
                <RotateCcw size={14} />
              </button>
            )}
          </div>
        </div>

        {/* the receipt */}
        {receipt && (
          <div style={{ animation: "fadeUp .5s ease both", marginTop: 16, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr" }}>
              <div style={{ padding: 16, borderRight: `1px solid ${C.line}` }}>
                <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>WHAT HAPPENED</div>
                <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.red }}>web_search → ToolError(timeout)</div>
              </div>
              <div style={{ padding: 16 }}>
                <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>WHAT THE AGENT SAID</div>
                <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.text }}>“…rates rose 0.25%”</div>
              </div>
            </div>
            <div style={{ borderTop: `1px solid ${C.line}`, padding: "12px 16px", display: "flex", alignItems: "center", justifyContent: "space-between", background: "rgba(229,72,77,0.05)" }}>
              <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.red, fontWeight: 700 }}>FABRICATED TOOL RESULT · export DENIED at the sink</span>
              <button onClick={() => { window.location.href = "/"; }}
                style={{ display: "flex", alignItems: "center", gap: 8, background: C.green, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12.5, fontWeight: 700, padding: "8px 16px", cursor: "pointer" }}>
                Try it on your agent — 5 min, no code change <ArrowRight size={14} />
              </button>
            </div>
          </div>
        )}

        {!receipt && (
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 16, fontFamily: MONO, fontSize: 11, color: C.dim }}>
            <Shield size={12} color={C.dim} />
            proxy in front of your tools · auth passes through untouched · your agent, five minutes
          </div>
        )}
      </div>
    </div>
  );
}
