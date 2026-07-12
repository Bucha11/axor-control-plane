import { useState } from "react";
import { Play, Share2, Check, Shield, ChevronDown, ChevronRight } from "lucide-react";

const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

// causal subgraph = only the nodes causally contributing to the discrepancy.
// The full run had 6 nodes; this case touches 3. That's the point.
const SUB = {
  scraper:  { x: 70,  y: 60,  label: "web-scraper",  role: "origin",    integrity: 0.0 },
  research: { x: 70,  y: 150, label: "researcher",   role: "conduit",   integrity: 0.41 },
  orch:     { x: 70,  y: 240, label: "orchestrator", role: "anchor",    integrity: 0.38 },
};
const EDGES = [["scraper", "research"], ["research", "orch"]];
const roleColor = { origin: C.red, conduit: C.amber, anchor: C.red, container: C.green };
const roleNote = {
  origin: "fault landed here",
  conduit: "propagated through — taint carried, not laundered",
  anchor: "claim reached a consequence here",
  container: "denied propagation here",
};

export default function App() {
  const [twin, setTwin] = useState(false); // false = governed, true = ungoverned twin
  const [openRank, setOpenRank] = useState(false);
  const [shared, setShared] = useState(false);

  // governed vs ungoverned differ only at the anchor's export boundary
  const contained = !twin;
  const anchorRole = twin ? "anchor" : "container"; // governed contains at orchestrator's export

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "28px 20px" }}>
      <div style={{ maxWidth: 620, margin: "0 auto" }}>
        <div className="flex items-center gap-6 mb-6">
          <span style={{ fontFamily: MONO, fontSize: 14, fontWeight: 700 }}>AXOR<span style={{ color: C.steel }}> CONTROL PLANE</span></span>
          <span style={{ fontFamily: MONO, fontSize: 12, color: C.text, borderBottom: `2px solid ${C.steel}`, paddingBottom: 2 }}>evidence · ev_042</span>
        </div>

        <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 4 }}>
          run_7c31 · 6-node tree · this case touches 3
        </div>
        <h1 style={{ fontSize: 20, fontWeight: 650, lineHeight: 1.35, margin: "0 0 18px" }}>
          The orchestrator's answer was fabricated — but the lie began two agents away.
        </h1>

        {/* the receipt: reality vs claim, at the anchor */}
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
          <div className="grid" style={{ gridTemplateColumns: "1fr 1fr" }}>
            <div style={{ padding: 14, borderRight: `1px solid ${C.line}` }}>
              <div style={{ fontFamily: MONO, fontSize: 9, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>OBSERVED REALITY (at origin)</div>
              <div style={{ fontFamily: MONO, fontSize: 12, color: C.red }}>web_search → ToolError(timeout)</div>
            </div>
            <div style={{ padding: 14 }}>
              <div style={{ fontFamily: MONO, fontSize: 9, color: C.dim, letterSpacing: "0.1em", marginBottom: 6 }}>CLAIM (at anchor)</div>
              <div style={{ fontFamily: MONO, fontSize: 12, color: C.text }}>"…rates rose 0.25%"</div>
            </div>
          </div>
          <div className="px-4 py-2.5 flex items-center justify-between" style={{ borderTop: `1px solid ${C.line}`, background: "rgba(229,72,77,0.05)" }}>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.red, fontWeight: 700 }}>FABRICATION · verdict at orchestrator</span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut }}>anchored at consequence, not at cause</span>
          </div>
        </div>

        {/* causal subgraph */}
        <div className="mt-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
          <div className="flex items-center justify-between px-4 py-2.5" style={{ borderBottom: `1px solid ${C.line}` }}>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.08em" }}>CAUSAL SUBGRAPH · the 3 nodes that produced this claim</span>
            <button onClick={() => setTwin(!twin)}
              style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${twin ? C.red : C.green}`, borderRadius: 4, color: twin ? C.red : C.green, fontFamily: MONO, fontSize: 10, padding: "3px 9px", cursor: "pointer" }}>
              <Shield size={10} /> {twin ? "ungoverned twin" : "governed"}
            </button>
          </div>

          <div className="flex" style={{ padding: "8px 4px" }}>
            <svg viewBox="0 0 140 300" style={{ width: 140, flexShrink: 0 }}>
              {EDGES.map(([a, b]) => (
                <g key={a + b}>
                  <path d={`M ${SUB[a].x} ${SUB[a].y} L ${SUB[b].x} ${SUB[b].y}`} stroke={C.amber} strokeWidth="1.5" strokeDasharray="4 3" />
                  <text x={SUB[a].x + 10} y={(SUB[a].y + SUB[b].y) / 2} fill={C.amber} fontSize="7" fontFamily={MONO}>taint→</text>
                </g>
              ))}
              {/* export edge from anchor — contained (governed) or escaped (twin) */}
              <path d={`M ${SUB.orch.x} ${SUB.orch.y} L ${SUB.orch.x} 292`} stroke={twin ? C.red : C.green} strokeWidth="1.5" strokeDasharray={twin ? "0" : "4 3"} />
              {contained ? (
                <>
                  <circle cx={SUB.orch.x} cy="280" r="9" fill={C.panel} stroke={C.green} strokeWidth="1.3" />
                  <text x={SUB.orch.x} y="283" textAnchor="middle" fontSize="9">🛡</text>
                  <text x={SUB.orch.x + 16} y="284" fill={C.green} fontSize="7.5" fontFamily={MONO}>CONTAINED</text>
                </>
              ) : (
                <text x={SUB.orch.x + 14} y="288" fill={C.red} fontSize="7.5" fontFamily={MONO}>→ SLACK (escaped)</text>
              )}

              {Object.entries(SUB).map(([id, n]) => {
                const role = id === "orch" ? anchorRole : n.role;
                const col = roleColor[role];
                return (
                  <g key={id}>
                    <circle cx={n.x} cy={n.y} r="15" fill={C.panel2} stroke={col} strokeWidth="1.6"
                      style={{ filter: `drop-shadow(0 0 4px ${col})` }} />
                    <text x={n.x + 22} y={n.y - 2} fill={C.text} fontSize="9" fontFamily={MONO}>{n.label}</text>
                    <text x={n.x + 22} y={n.y + 9} fill={col} fontSize="7.5" fontFamily={MONO}>{role} · integ {n.integrity.toFixed(2)}</text>
                  </g>
                );
              })}
            </svg>

            {/* role legend / narrative */}
            <div className="flex-1 flex flex-col justify-center gap-2.5 pr-3">
              {["origin", "conduit", contained ? "container" : "anchor"].map((r, i) => (
                <div key={r} className="flex items-start gap-2">
                  <span style={{ width: 8, height: 8, borderRadius: 4, background: roleColor[r], marginTop: 3, flexShrink: 0 }} />
                  <div>
                    <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, fontWeight: 600 }}>{r}</span>
                    <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, lineHeight: 1.4 }}>{roleNote[r]}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="px-4 py-2" style={{ borderTop: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 10, color: C.dim }}>
            {contained
              ? "one case, three nodes — the fabrication propagated but was contained at the orchestrator's export"
              : "ungoverned: same three nodes, same fabrication — reached Slack. This is the twin the governed case is aligned against."}
          </div>
        </div>

        {/* influence ranking, folded */}
        <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
          <button onClick={() => setOpenRank(!openRank)} className="flex items-center gap-2 px-4 py-3 w-full"
            style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 11.5, cursor: "pointer" }}>
            {openRank ? <ChevronDown size={13} /> : <ChevronRight size={13} />} influence ranking — which upstream value most drove the anchor's claim
          </button>
          {openRank && (
            <div className="px-4 pb-3" style={{ paddingLeft: 34 }}>
              {[["v_8a12 · scraper's fabricated result", 0.71], ["research summary (derived)", 0.22], ["orchestrator prompt", 0.07]].map(([v, w]) => (
                <div key={v} className="flex items-center gap-3 py-1">
                  <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, flex: 1 }}>{v}</span>
                  <span style={{ width: 80, height: 4, background: C.panel2, borderRadius: 2, overflow: "hidden" }}>
                    <span style={{ display: "block", width: `${w * 100}%`, height: "100%", background: w > 0.5 ? C.red : C.amber }} />
                  </span>
                  <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut, width: 32 }}>{w.toFixed(2)}</span>
                </div>
              ))}
              <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 6 }}>ranked by subgraph ablation — deterministic, bounded by causal-chain length</div>
            </div>
          )}
        </div>

        {/* actions */}
        <div className="flex items-center gap-3 mt-4">
          <button style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.steel}`, borderRadius: 5, color: C.steel, fontFamily: MONO, fontSize: 11.5, padding: "7px 13px", cursor: "pointer" }}>
            <Play size={12} /> Open in replay at origin
          </button>
          <button onClick={() => { setShared(true); setTimeout(() => setShared(false), 1500); }}
            style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: shared ? C.green : C.mut, fontFamily: MONO, fontSize: 11.5, padding: "7px 13px", cursor: "pointer" }}>
            {shared ? <Check size={12} /> : <Share2 size={12} />} {shared ? "link copied" : "Share case"}
          </button>
          <span style={{ flex: 1 }} />
          <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>observations only · no raw bodies</span>
        </div>

        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 16, lineHeight: 1.7 }}>
          One discrepancy, one case — anchored where the claim reached a consequence. The intermediate fabrication
          at the researcher is a <span style={{ color: C.amber }}>conduit node in this case's subgraph</span>, not a separate case.
          The subgraph is derived from the trace on open, never stored.
        </div>
      </div>
    </div>
  );
}
