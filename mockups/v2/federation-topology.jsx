import { useState } from "react";
import { GitBranch, Shield, Lock, AlertTriangle, X } from "lucide-react";

const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC", violet: "#9B8CCC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// our federation (governed) + one foreign peer (opaque)
const NODES = {
  orch:     { x: 190, y: 70,  label: "orchestrator", level: "NORMAL", heat: 0.05, kind: "self" },
  research: { x: 110, y: 175, label: "researcher",   level: "CAUTIOUS", heat: 0.4, kind: "self" },
  writer:   { x: 270, y: 175, label: "writer",       level: "NORMAL", heat: 0.05, kind: "self" },
  scraper:  { x: 110, y: 285, label: "web-scraper",  level: "RESTRICTED", heat: 0.82, kind: "self" },
  peer:     { x: 400, y: 175, label: "partner-agent", level: "L1", kind: "peer" },
};
// edges: delegation (solid), lateral (dashed, intra), inter (double, to peer)
const EDGES = [
  { a: "orch", b: "research", kind: "delegation" },
  { a: "orch", b: "writer", kind: "delegation" },
  { a: "research", b: "scraper", kind: "delegation" },
  { a: "research", b: "writer", kind: "lateral" },
  { a: "writer", b: "peer", kind: "inter" },
];

const lvlColor = (l) => ({ NORMAL: C.green, CAUTIOUS: C.amber, RESTRICTED: C.red, TERMINAL: C.red, L0: C.dim, L1: C.steel, L2: C.violet }[l] || C.mut);

export default function App() {
  const [sel, setSel] = useState(null);
  const node = sel ? NODES[sel] : null;

  const edgePath = (e) => {
    const a = NODES[e.a], b = NODES[e.b];
    return `M ${a.x} ${a.y} L ${b.x} ${b.y}`;
  };

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "28px 20px" }}>
      <div style={{ maxWidth: 720, margin: "0 auto" }}>
        <div className="flex items-center gap-6 mb-6">
          <span style={{ fontFamily: MONO, fontSize: 14, fontWeight: 700 }}>AXOR<span style={{ color: C.steel }}> CONTROL PLANE</span></span>
          <div className="flex gap-4">
            <span style={{ fontFamily: MONO, fontSize: 13, color: C.dim }}>list</span>
            <span style={{ fontFamily: MONO, fontSize: 13, color: C.text, borderBottom: `2px solid ${C.steel}`, paddingBottom: 2 }}>graph</span>
          </div>
        </div>

        <h1 style={{ fontSize: 21, fontWeight: 650, margin: "0 0 2px" }}>
          {Object.values(NODES).some((n) => n.kind === "self" && n.level === "RESTRICTED")
            ? <>One agent restricted · one external peer connected.</>
            : <>Federation healthy.</>}
        </h1>
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 16 }}>
          4 governed nodes · 1 inter-federation peer · graph mode shows what a list can't: the boundary and its edges
        </div>

        <div style={{ position: "relative", background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
          <svg viewBox="0 0 480 340" style={{ width: "100%", display: "block" }}>
            {/* federation enclosure */}
            <rect x="40" y="30" width="290" height="290" rx="14" fill="rgba(127,168,204,0.03)" stroke={C.steel} strokeWidth="1" strokeDasharray="2 4" />
            <text x="52" y="50" fill={C.steel} fontSize="9" fontFamily={MONO} opacity="0.7">FEDERATION · your keyset</text>

            {/* edges */}
            {EDGES.map((e) => {
              const inter = e.kind === "inter";
              const lateral = e.kind === "lateral";
              return (
                <g key={e.a + e.b}>
                  {inter ? (
                    <>
                      <path d={edgePath(e)} stroke={C.violet} strokeWidth="2.5" fill="none" opacity="0.35" />
                      <path d={edgePath(e)} stroke={C.bg} strokeWidth="0.8" fill="none" />
                    </>
                  ) : (
                    <path d={edgePath(e)} stroke={lateral ? C.dim : C.line} strokeWidth="1.5"
                      strokeDasharray={lateral ? "4 3" : "0"} fill="none" />
                  )}
                  {/* boundary marker on inter edge */}
                  {inter && (
                    <g>
                      <circle cx="335" cy="175" r="8" fill={C.panel} stroke={C.violet} strokeWidth="1.2" />
                      <text x="335" y="178" textAnchor="middle" fontSize="9">⇥</text>
                    </g>
                  )}
                </g>
              );
            })}

            {/* nodes */}
            {Object.entries(NODES).map(([id, n]) => {
              const isPeer = n.kind === "peer";
              const ring = isPeer ? C.violet : lvlColor(n.level);
              const hot = !isPeer && n.heat > 0.7;
              return (
                <g key={id} onClick={() => setSel(sel === id ? null : id)} style={{ cursor: "pointer" }}>
                  {isPeer ? (
                    // opaque peer: hexagon-ish square, distinct glyph
                    <rect x={n.x - 15} y={n.y - 15} width="30" height="30" rx="4" fill={C.panel2} stroke={ring} strokeWidth="1.5"
                      transform={`rotate(45 ${n.x} ${n.y})`} />
                  ) : (
                    <circle cx={n.x} cy={n.y} r="17" fill={sel === id ? "rgba(127,168,204,0.12)" : C.panel2} stroke={ring} strokeWidth={hot ? "2" : "1.5"}
                      style={hot ? { filter: `drop-shadow(0 0 5px ${ring})` } : {}} />
                  )}
                  {isPeer && <Lock x={n.x - 5} y={n.y - 5} width={10} height={10} color={C.violet} />}
                  <text x={n.x} y={isPeer ? n.y + 30 : n.y + 30} textAnchor="middle" fill={sel === id ? C.text : C.mut} fontSize="9.5" fontFamily={MONO}>{n.label}</text>
                  {!isPeer && n.level !== "NORMAL" && (
                    <text x={n.x} y={n.y + 41} textAnchor="middle" fill={lvlColor(n.level)} fontSize="8" fontFamily={MONO}>{n.level}</text>
                  )}
                  {isPeer && (
                    <text x={n.x} y={n.y + 41} textAnchor="middle" fill={C.steel} fontSize="8" fontFamily={MONO}>{n.level} · attested</text>
                  )}
                </g>
              );
            })}
          </svg>

          {/* legend */}
          <div className="flex items-center gap-4 px-4 py-2" style={{ borderTop: `1px solid ${C.line}` }}>
            {[["delegation", C.line, "0"], ["lateral (intra)", C.dim, "4 3"]].map(([l, col, d]) => (
              <div key={l} className="flex items-center gap-1.5">
                <svg width="18" height="4"><line x1="0" y1="2" x2="18" y2="2" stroke={col} strokeWidth="1.5" strokeDasharray={d} /></svg>
                <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>{l}</span>
              </div>
            ))}
            <div className="flex items-center gap-1.5">
              <svg width="18" height="6"><line x1="0" y1="3" x2="18" y2="3" stroke={C.violet} strokeWidth="2.5" opacity="0.4" /></svg>
              <span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>inter-federation</span>
            </div>
            <div className="flex items-center gap-1.5">
              <Lock size={10} color={C.violet} /><span style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim }}>opaque peer</span>
            </div>
          </div>
        </div>

        {/* selection detail */}
        {node && (
          <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
            {node.kind === "self" ? (
              <>
                <div className="flex items-center justify-between mb-2">
                  <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>{node.label}</span>
                  <span style={{ fontFamily: MONO, fontSize: 10, color: lvlColor(node.level), fontWeight: 700 }}>{node.level} · heat {node.heat.toFixed(2)}</span>
                </div>
                <div className="flex gap-2">
                  <button style={{ display: "flex", alignItems: "center", gap: 5, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: "6px 12px", cursor: "pointer" }}>Pause</button>
                  <button style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 11, cursor: "pointer" }}>more…</button>
                </div>
                {node.level === "RESTRICTED" && (
                  <div className="mt-3 pt-3" style={{ borderTop: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 11, color: C.mut }}>
                    RESTRICTED · 1 uncovered fact. <span style={{ color: C.steel, cursor: "pointer" }}>Review & attest →</span>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="flex items-center gap-2 mb-2">
                  <Lock size={13} color={C.violet} />
                  <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>{node.label}</span>
                  <span style={{ fontFamily: MONO, fontSize: 10, color: C.steel }}>{node.level} · governance-attested</span>
                </div>
                <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
                  Foreign federation — different keyset. We govern <span style={{ color: C.text }}>our edge to it</span>, not its internals.
                  Inbound values re-derived at L1 (attribution, not trust). No pause / inject / attest — structurally, not greyed:
                  their agent is not ours to steer.
                </div>
                <div className="mt-3 pt-3 flex items-center gap-2" style={{ borderTop: `1px solid ${C.line}` }}>
                  <AlertTriangle size={11} color={C.amber} />
                  <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>
                    controllable here: <span style={{ color: C.text }}>our writer's sends to this peer</span> (stop / gate) — the outbound edge, ours
                  </span>
                </div>
              </>
            )}
          </div>
        )}
        {!node && (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 12, lineHeight: 1.7 }}>
            Tap a node. Inside the boundary: full control. The diamond is a peer under a different keyset —
            opaque by design, we measure and gate only our edge to it. This is the one view a list cannot carry:
            edges that cross the federation boundary.
          </div>
        )}
      </div>
    </div>
  );
}
