import { useState, useEffect } from "react";
import { Play, RotateCcw, ArrowRight, Shield } from "lucide-react";

const C = {
  bg: "#0E1216", panel: "#151A20", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// one topology, replayed on both sides (deterministic, recorded — spec §13)
// leaf: web-scraper (fault lands here) -> researcher -> orchestrator -> export(slack)
const NODES = {
  scraper: { x: 60,  y: 200, label: "scraper" },
  research:{ x: 60,  y: 120, label: "researcher" },
  orch:    { x: 60,  y: 40,  label: "orchestrator" },
};
const EDGES = [["scraper","research"],["research","orch"]];

// scripted steps; each side reads the same script, diverges at containment
const STEPS = [
  { cap: "fault injected at the leaf: web_search → ToolError(timeout)", fault: true },
  { cap: "scraper fabricates a result instead of reporting the failure", fabricate: "scraper" },
  { cap: "the fabrication (tainted) is delegated upward…", flow: ["scraper","research"] },
  { cap: "researcher folds it into its answer, taint carried", fabricate: "research", flow2: ["research","orch"] },
  { cap: "orchestrator assembles the final answer…", fabricate: "orch" },
  { cap: "…and reaches the export boundary", exportReach: true },
  { cap: "", verdict: true }, // divergence rendered here
];

export default function App() {
  const [playing, setPlaying] = useState(false);
  const [i, setI] = useState(-1);

  useEffect(() => {
    if (!playing || i >= STEPS.length - 1) return;
    const t = setTimeout(() => setI((x) => x + 1), i < 0 ? 250 : (i === 5 ? 1400 : 1500));
    return () => clearTimeout(t);
  }, [playing, i]);

  const done = (k, v) => i >= 0 && STEPS.slice(0, i + 1).some((s) => (v ? s[k] === v : s[k]));
  const step = i >= 0 ? STEPS[i] : {};
  const verdict = done("verdict");

  const Tree = ({ governed }) => {
    // ungoverned: fabrication propagates all the way to export (red at every node + escaped)
    // governed: contained at the export boundary (orchestrator denied), nodes tainted but export blocked
    const fabricated = (id) => done("fabricate", id);
    const escaped = verdict && !governed;
    const contained = verdict && governed;

    return (
      <div style={{ flex: 1, background: C.panel, border: `1px solid ${governed ? (contained ? C.green : C.line) : (escaped ? C.red : C.line)}`, borderRadius: 10, overflow: "hidden" }}>
        <div className="flex items-center justify-between px-3 py-2" style={{ borderBottom: `1px solid ${C.line}` }}>
          <div className="flex items-center gap-2">
            <Shield size={12} color={governed ? C.green : C.dim} />
            <span style={{ fontFamily: MONO, fontSize: 11, color: governed ? C.green : C.mut, fontWeight: 700 }}>
              {governed ? "GOVERNED" : "UNGOVERNED"}
            </span>
          </div>
          {verdict && (
            <span style={{ fontFamily: MONO, fontSize: 10, fontWeight: 700, color: governed ? C.green : C.red }}>
              {governed ? "CONTAINED" : "FABRICATION ESCAPED"}
            </span>
          )}
        </div>
        <svg viewBox="0 0 120 260" style={{ width: "100%", display: "block", height: 240 }}>
          {/* edges */}
          {EDGES.map(([a, b]) => {
            const active = done("flow", null) && false;
            return <line key={a + b} x1={NODES[a].x} y1={NODES[a].y} x2={NODES[b].x} y2={NODES[b].y}
              stroke={C.line} strokeWidth="1.5" />;
          })}
          {/* export edge from orchestrator */}
          <line x1={NODES.orch.x} y1={NODES.orch.y} x2={NODES.orch.x} y2={8}
            stroke={done("exportReach") ? (governed ? C.red : C.red) : C.line} strokeWidth="1.5"
            strokeDasharray={governed && contained ? "4 3" : "0"} />

          {/* travelling pulses */}
          {playing && step.flow && (
            <circle key={"f1" + i} r="4" fill={C.amber}>
              <animateMotion dur="1.4s" fill="freeze"
                path={`M ${NODES[step.flow[0]].x} ${NODES[step.flow[0]].y} L ${NODES[step.flow[1]].x} ${NODES[step.flow[1]].y}`} />
            </circle>
          )}
          {playing && step.flow2 && (
            <circle key={"f2" + i} r="4" fill={C.amber}>
              <animateMotion dur="1.4s" fill="freeze"
                path={`M ${NODES[step.flow2[0]].x} ${NODES[step.flow2[0]].y} L ${NODES[step.flow2[1]].x} ${NODES[step.flow2[1]].y}`} />
            </circle>
          )}

          {/* containment shield on governed export edge */}
          {contained && (
            <g>
              <circle cx={NODES.orch.x} cy={24} r="11" fill={C.panel} stroke={C.green} strokeWidth="1.5" />
              <text x={NODES.orch.x} y={28} textAnchor="middle" fontSize="11">🛡</text>
            </g>
          )}
          {escaped && (
            <text x={NODES.orch.x} y={16} textAnchor="middle" fill={C.red} fontSize="8" fontFamily={MONO} fontWeight="700">→ SLACK</text>
          )}

          {/* nodes */}
          {Object.entries(NODES).map(([id, n]) => {
            const fab = fabricated(id);
            const isFaultLeaf = id === "scraper" && done("fault");
            let ring = C.line, glow = false;
            if (governed) {
              if (id === "orch" && contained) { ring = C.green; glow = true; }
              else if (fab) { ring = C.amber; } // tainted but held
            } else {
              if (fab) { ring = C.red; glow = true; }
            }
            if (isFaultLeaf && !fab) ring = C.red;
            return (
              <g key={id}>
                <circle cx={n.x} cy={n.y} r="16" fill={C.panel} stroke={ring} strokeWidth="1.5"
                  style={glow ? { filter: `drop-shadow(0 0 5px ${ring})` } : {}} />
                <text x={n.x + 22} y={n.y + 4} fill={C.mut} fontSize="9" fontFamily={MONO}>{n.label}</text>
              </g>
            );
          })}
          {/* fault marker on leaf */}
          {done("fault") && !verdict && (
            <text x={NODES.scraper.x} y={NODES.scraper.y + 32} textAnchor="middle" fill={C.red} fontSize="8" fontFamily={MONO}>ToolError</text>
          )}
        </svg>
      </div>
    );
  };

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", display: "flex", flexDirection: "column", alignItems: "center", padding: "44px 20px" }}>
      <style>{`@keyframes fadeUp { from { opacity:0; transform:translateY(8px);} to {opacity:1;transform:none;} }`}</style>
      <div style={{ maxWidth: 720, width: "100%" }}>
        <div style={{ fontFamily: MONO, fontSize: 12, color: C.steel, letterSpacing: "0.1em", marginBottom: 12 }}>AXOR CONTROL PLANE</div>
        <h1 style={{ fontSize: 28, fontWeight: 700, lineHeight: 1.25, margin: "0 0 8px" }}>
          One bad tool call. Three agents.<br />
          <span style={{ color: C.mut }}>Watch the lie spread — then watch it stop.</span>
        </h1>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginBottom: 20 }}>
          same recorded fault, replayed over both topologies — deterministic, no live model
        </div>

        <div className="flex gap-3">
          <Tree governed={false} />
          <Tree governed={true} />
        </div>

        {/* caption / control */}
        <div className="mt-3 px-4 py-3 flex items-center justify-between" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, minHeight: 52 }}>
          {!playing ? (
            <button onClick={() => { setPlaying(true); setI(-1); }}
              style={{ display: "flex", alignItems: "center", gap: 8, background: C.steel, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12.5, fontWeight: 700, padding: "8px 18px", cursor: "pointer" }}>
              <Play size={14} /> Run the recording
            </button>
          ) : verdict ? (
            <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, animation: "fadeUp .4s ease both" }}>
              Same fabrication in both. Left: it reached Slack. Right: <span style={{ color: C.green }}>denied at the export boundary</span> — the agent failed honestly instead of lying.
            </span>
          ) : (
            <span key={i} style={{ fontFamily: MONO, fontSize: 12, color: C.mut, animation: "fadeUp .3s ease both" }}>{step.cap}</span>
          )}
          {playing && <button onClick={() => setI(-1)} style={{ background: "none", border: "none", color: C.dim, cursor: "pointer" }}><RotateCcw size={14} /></button>}
        </div>

        {verdict && (
          <div style={{ animation: "fadeUp .5s ease both", marginTop: 14, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, padding: 16 }}>
            <div className="flex items-center justify-between mb-3">
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em" }}>CONTAINMENT — one fault, measured at each boundary</span>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.green, fontWeight: 700 }}>2/2 boundaries held</span>
            </div>
            <div className="flex flex-col gap-1.5">
              {[
                ["scraper → researcher", "carried, not laundered", C.amber],
                ["researcher → orchestrator", "carried, not laundered", C.amber],
                ["orchestrator → export", "DENIED — contained here", C.green],
              ].map(([edge, note, col]) => (
                <div key={edge} className="flex items-center justify-between">
                  <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>{edge}</span>
                  <span style={{ fontFamily: MONO, fontSize: 10.5, color: col }}>{note}</span>
                </div>
              ))}
            </div>
            <div className="mt-3 pt-3 flex items-center justify-between" style={{ borderTop: `1px solid ${C.line}` }}>
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
                systemic outcome: <span style={{ color: C.red }}>fabricated_failure</span> → <span style={{ color: C.green }}>honest_failure</span>
              </span>
              <button style={{ display: "flex", alignItems: "center", gap: 8, background: C.green, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12, fontWeight: 700, padding: "7px 14px", cursor: "pointer" }}>
                Try it on your agents <ArrowRight size={13} />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
