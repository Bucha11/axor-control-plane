import { useState } from "react";
import { Lock, AlertTriangle, ChevronRight, CornerDownLeft, Activity, Pause, Play, Shield } from "lucide-react";

const C = {
  bg: "#0F1319", panel: "#161B22", panel2: "#12161C", line: "#242C35",
  text: "#D2DAE1", mut: "#78848F", dim: "#495159",
  red: "#E5484D", amber: "#F2A33C", green: "#46A758", steel: "#7FA8CC", violet: "#9B8CCC",
};
const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";
const lvl = (l) => ({ NORMAL: C.green, CAUTIOUS: C.amber, RESTRICTED: C.red, TERMINAL: C.red, L0: C.dim, L1: C.steel, L2: C.violet }[l] || C.mut);

// hierarchical world: each node may contain its own graph.
// A node is: { id, label, level, heat, kind, x, y, children?, edges?, events?, taint?, peer? }
const WORLD = {
  id: "root", label: "federation", kind: "federation",
  nodes: {
    orch:     { x: 190, y: 70,  label: "orchestrator", level: "NORMAL", heat: 0.05, kind: "self", hasInside: true },
    research: { x: 110, y: 175, label: "researcher",   level: "CAUTIOUS", heat: 0.4, kind: "self", hasInside: true },
    writer:   { x: 270, y: 175, label: "writer",       level: "NORMAL", heat: 0.05, kind: "self", hasInside: false },
    scraper:  { x: 110, y: 285, label: "web-scraper",  level: "RESTRICTED", heat: 0.82, kind: "self", hasInside: false },
    peer:     { x: 400, y: 175, label: "partner-agent",level: "L1", kind: "peer" },
  },
  edges: [
    { a: "orch", b: "research", kind: "delegation" },
    { a: "orch", b: "writer", kind: "delegation" },
    { a: "research", b: "scraper", kind: "delegation" },
    { a: "research", b: "writer", kind: "lateral" },
    { a: "writer", b: "peer", kind: "inter" },
  ],
};

// inside-views: a node's own subtree OR (leaf) its event/governance interior
const INSIDE = {
  orch: {
    kind: "subtree",
    nodes: {
      plan:  { x: 190, y: 70,  label: "planner", level: "NORMAL", heat: 0.03, kind: "self", hasInside: false },
      research: { x: 110, y: 190, label: "researcher", level: "CAUTIOUS", heat: 0.4, kind: "self", hasInside: true },
      writer:{ x: 270, y: 190, label: "writer", level: "NORMAL", heat: 0.05, kind: "self", hasInside: false },
    },
    edges: [{ a: "plan", b: "research", kind: "delegation" }, { a: "plan", b: "writer", kind: "delegation" }],
  },
  research: {
    kind: "subtree",
    nodes: {
      scraper: { x: 190, y: 90, label: "web-scraper", level: "RESTRICTED", heat: 0.82, kind: "self", hasInside: false },
      summ:    { x: 190, y: 220, label: "summarizer", level: "CAUTIOUS", heat: 0.31, kind: "self", hasInside: false },
    },
    edges: [{ a: "scraper", b: "summ", kind: "delegation" }],
  },
};

// leaf interior: event stream + governance state (drill into a node with no children)
const LEAF = {
  scraper: {
    level: "RESTRICTED", heat: 0.82,
    facts: [["f_301 external-read burst", "sev 2", "uncovered"], ["f_305 canary echo", "sev 2", "uncovered"]],
    events: [
      ["12:04:02", "tool", "web_search → ToolError(timeout)", "deny"],
      ["12:04:03", "claim", '"rates rose 0.25%"', "deny"],
      ["12:04:03", "gate", "claim-vs-reality → FABRICATION", "deny"],
      ["12:04:08", "gate", "taint→export → DENY", "deny"],
    ],
  },
  writer: {
    level: "NORMAL", heat: 0.05, facts: [],
    events: [["12:04:09", "tool", "report.finalize", "ok"], ["12:04:09", "gate", "capability → PASS", "ok"]],
  },
};
const sc = (s) => (s === "deny" ? C.red : s === "warn" ? C.amber : C.green);

export default function App() {
  // stack of {id, label} we've descended through; [] = top federation view
  const [stack, setStack] = useState([]);
  const [sel, setSel] = useState(null);
  const [paused, setPaused] = useState({});   // node id -> bool
  const [attested, setAttested] = useState({}); // node id -> bool
  const togglePause = (id) => setPaused((p) => ({ ...p, [id]: !p[id] }));

  const depth = stack.length;
  const here = depth === 0 ? WORLD : (INSIDE[stack[depth - 1].id] || null);
  const leaf = depth > 0 && !here ? LEAF[stack[depth - 1].id] : null;

  const descend = (id, label) => { setStack([...stack, { id, label }]); setSel(null); };
  const goto = (n) => { setStack(stack.slice(0, n)); setSel(null); };

  const edgePath = (nodes, e) => `M ${nodes[e.a].x} ${nodes[e.a].y} L ${nodes[e.b].x} ${nodes[e.b].y}`;

  return (
    <div style={{ minHeight: "100vh", background: C.bg, color: C.text, fontFamily: "Inter, system-ui, sans-serif", padding: "28px 20px" }}>
      <div style={{ maxWidth: 700, margin: "0 auto" }}>
        <div className="flex items-center gap-6 mb-5">
          <span style={{ fontFamily: MONO, fontSize: 14, fontWeight: 700 }}>AXOR<span style={{ color: C.steel }}> CONTROL PLANE</span></span>
          <span style={{ fontFamily: MONO, fontSize: 13, color: C.text, borderBottom: `2px solid ${C.steel}`, paddingBottom: 2 }}>graph</span>
        </div>

        {/* breadcrumb */}
        <div className="flex items-center gap-1.5 mb-4" style={{ fontFamily: MONO, fontSize: 12 }}>
          <span onClick={() => goto(0)} style={{ color: depth === 0 ? C.text : C.steel, cursor: "pointer" }}>federation</span>
          {stack.map((s, i) => (
            <span key={i} className="flex items-center gap-1.5">
              <ChevronRight size={12} color={C.dim} />
              <span onClick={() => goto(i + 1)} style={{ color: i === depth - 1 ? C.text : C.steel, cursor: "pointer" }}>{s.label}</span>
            </span>
          ))}
        </div>

        {/* ---- GRAPH view (federation or subtree) ---- */}
        {here && (
          <>
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 12 }}>
              {depth === 0
                ? "4 governed nodes · 1 inter-federation peer · tap to inspect, double-tap to descend"
                : `inside ${stack[depth - 1].label} · its subtree · tap to descend further`}
            </div>
            <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
              <svg viewBox="0 0 480 340" style={{ width: "100%", display: "block" }}>
                {depth === 0 && (
                  <>
                    <rect x="40" y="30" width="290" height="290" rx="14" fill="rgba(127,168,204,0.03)" stroke={C.steel} strokeWidth="1" strokeDasharray="2 4" />
                    <text x="52" y="50" fill={C.steel} fontSize="9" fontFamily={MONO} opacity="0.7">FEDERATION · your keyset</text>
                  </>
                )}
                {here.edges.map((e) => {
                  const inter = e.kind === "inter", lateral = e.kind === "lateral";
                  return inter ? (
                    <g key={e.a + e.b}>
                      <path d={edgePath(here.nodes, e)} stroke={C.violet} strokeWidth="2.5" fill="none" opacity="0.35" />
                      <circle cx="335" cy="175" r="8" fill={C.panel} stroke={C.violet} strokeWidth="1.2" />
                      <text x="335" y="178" textAnchor="middle" fontSize="9">⇥</text>
                    </g>
                  ) : (
                    <path key={e.a + e.b} d={edgePath(here.nodes, e)} stroke={lateral ? C.dim : C.line} strokeWidth="1.5" strokeDasharray={lateral ? "4 3" : "0"} fill="none" />
                  );
                })}
                {Object.entries(here.nodes).map(([id, n]) => {
                  const isPeer = n.kind === "peer";
                  const ring = isPeer ? C.violet : lvl(n.level);
                  const hot = !isPeer && n.heat > 0.7;
                  const canDescend = n.hasInside || (!isPeer && LEAF[id]);
                  return (
                    <g key={id} style={{ cursor: "pointer" }}
                      onClick={() => setSel(sel === id ? null : id)}
                      onDoubleClick={() => canDescend && descend(id, n.label)}>
                      {isPeer ? (
                        <rect x={n.x - 15} y={n.y - 15} width="30" height="30" rx="4" fill={C.panel2} stroke={ring} strokeWidth="1.5" transform={`rotate(45 ${n.x} ${n.y})`} />
                      ) : (
                        <>
                          <circle cx={n.x} cy={n.y} r="17" fill={sel === id ? "rgba(127,168,204,0.12)" : C.panel2} stroke={ring} strokeWidth={hot ? "2" : "1.5"} style={hot ? { filter: `drop-shadow(0 0 5px ${ring})` } : {}} />
                          {/* inner ring hints "has an inside" */}
                          {canDescend && <circle cx={n.x} cy={n.y} r="12" fill="none" stroke={ring} strokeWidth="0.6" opacity="0.4" />}
                        </>
                      )}
                      {isPeer && <Lock x={n.x - 5} y={n.y - 5} width={10} height={10} color={C.violet} />}
                      <text x={n.x} y={n.y + 30} textAnchor="middle" fill={sel === id ? C.text : C.mut} fontSize="9.5" fontFamily={MONO}>{n.label}</text>
                      {!isPeer && n.level !== "NORMAL" && <text x={n.x} y={n.y + 41} textAnchor="middle" fill={lvl(n.level)} fontSize="8" fontFamily={MONO}>{n.level}</text>}
                      {isPeer && <text x={n.x} y={n.y + 41} textAnchor="middle" fill={C.steel} fontSize="8" fontFamily={MONO}>{n.level} · attested</text>}
                    </g>
                  );
                })}
              </svg>
              <div className="flex items-center gap-4 px-4 py-2" style={{ borderTop: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
                <span>solid = delegation</span><span>dashed = lateral</span>
                {depth === 0 && <span style={{ color: C.violet }}>double = inter</span>}
                <span style={{ marginLeft: "auto", color: C.dim }}>◎ = has an inside · double-tap to enter</span>
              </div>
            </div>
          </>
        )}

        {/* ---- LEAF interior: descended into a node with no subtree ---- */}
        {leaf && (
          <>
            <div className="flex items-center gap-3 mb-3">
              <div className="flex items-center gap-2">
                <Activity size={13} color={lvl(leaf.level)} />
                <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>{stack[depth - 1].label}</span>
              </div>
              <span style={{ fontFamily: MONO, fontSize: 10, color: lvl(leaf.level), fontWeight: 700 }}>{leaf.level} · heat {leaf.heat.toFixed(2)}</span>
              <span style={{ flex: 1 }} />
              <button onClick={() => togglePause(stack[depth - 1].id)} style={{ display: "flex", alignItems: "center", gap: 5, background: paused[stack[depth - 1].id] ? "rgba(242,163,60,0.12)" : C.bg, border: `1px solid ${paused[stack[depth - 1].id] ? C.amber : C.line}`, borderRadius: 5, color: paused[stack[depth - 1].id] ? C.amber : C.text, fontFamily: MONO, fontSize: 11, padding: "5px 11px", cursor: "pointer" }}>
                {paused[stack[depth - 1].id] ? <><Play size={11} /> Resume</> : <><Pause size={11} /> Pause</>}
              </button>
            </div>
            {/* event stream */}
            <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
              <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", padding: "8px 14px", borderBottom: `1px solid ${C.line}` }}>THIS NODE'S EVENTS</div>
              {leaf.events.map((e, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2" style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
                  <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, width: 60 }}>{e[0]}</span>
                  <span style={{ fontFamily: MONO, fontSize: 9, color: C.mut, width: 40 }}>{e[1]}</span>
                  <span style={{ fontFamily: MONO, fontSize: 11.5, color: e[3] === "deny" ? C.text : C.mut, flex: 1 }}>{e[2]}</span>
                  <span style={{ width: 7, height: 7, borderRadius: 4, background: sc(e[3]) }} />
                </div>
              ))}
            </div>
            {/* governance interior */}
            {leaf.facts.length > 0 && (() => {
              const nid = stack[depth - 1].id;
              const isAtt = attested[nid];
              const shownLevel = isAtt ? "NORMAL" : leaf.level;
              return (
                <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
                  <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 8 }}>GOVERNANCE STATE · level = max(severity(uncovered))</div>
                  {leaf.facts.map((f) => (
                    <div key={f[0]} className="flex items-center justify-between py-0.5">
                      <span style={{ fontFamily: MONO, fontSize: 11, color: isAtt ? C.dim : C.text, textDecoration: isAtt ? "line-through" : "none" }}>{f[0]}</span>
                      <span style={{ fontFamily: MONO, fontSize: 10, color: isAtt ? C.green : C.amber }}>{f[1]} · {isAtt ? "attested" : f[2]}</span>
                    </div>
                  ))}
                  <div className="mt-2 pt-2 flex items-center gap-3" style={{ borderTop: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 11 }}>
                    <span><span style={{ color: C.mut }}>→ </span><span style={{ color: lvl(shownLevel), fontWeight: 700 }}>{shownLevel}</span></span>
                    {isAtt ? (
                      <span style={{ color: C.green, fontSize: 10 }}>recomputed after attestation by op_dmitrii</span>
                    ) : (
                      <button onClick={() => setAttested((a) => ({ ...a, [nid]: true }))}
                        style={{ background: "none", border: `1px solid ${C.steel}`, borderRadius: 4, color: C.steel, fontFamily: MONO, fontSize: 10.5, padding: "3px 10px", cursor: "pointer" }}>
                        Review & attest →
                      </button>
                    )}
                  </div>
                </div>
              );
            })()}
          </>
        )}

        {/* selection card (graph views only) */}
        {here && sel && (
          <SelCard node={here.nodes[sel]} paused={!!paused[sel]} onPause={() => togglePause(sel)} onDescend={() => (here.nodes[sel].hasInside || LEAF[sel]) && descend(sel, here.nodes[sel].label)} />
        )}

        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 14, lineHeight: 1.7 }}>
          {depth === 0
            ? "The tree is nested: an orchestrator contains its sub-agents, each with its own world. Double-tap a ◎ node to descend; the breadcrumb walks you back out."
            : "Same rules at every depth — carried taint, local gates. You're looking at one node's interior; the parent above still governs the edge you came in through."}
        </div>
      </div>
    </div>
  );
}

function SelCard({ node, paused, onPause, onDescend }) {
  const isPeer = node.kind === "peer";
  return (
    <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
      {isPeer ? (
        <>
          <div className="flex items-center gap-2 mb-2">
            <Lock size={13} color={C.violet} />
            <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>{node.label}</span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.steel }}>{node.level} · governance-attested</span>
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
            Foreign keyset — opaque by design. No inside to enter: we have no causal visibility past the boundary,
            and no authority to assert its internals. We govern only <span style={{ color: C.text }}>our edge</span> to it.
          </div>
        </>
      ) : (
        <>
          <div className="flex items-center justify-between mb-2">
            <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>{node.label}</span>
            <span style={{ fontFamily: MONO, fontSize: 10, color: lvl(node.level), fontWeight: 700 }}>{node.level} · heat {node.heat.toFixed(2)}</span>
          </div>
          <div className="flex gap-2">
            <button onClick={onPause} style={{ display: "flex", alignItems: "center", gap: 5, background: paused ? "rgba(242,163,60,0.12)" : C.bg, border: `1px solid ${paused ? C.amber : C.line}`, borderRadius: 5, color: paused ? C.amber : C.text, fontFamily: MONO, fontSize: 11, padding: "6px 12px", cursor: "pointer" }}>
              {paused ? <><Play size={11} /> Resume</> : <><Pause size={11} /> Pause</>}
            </button>
            {(node.hasInside || node.kind === "self") && (
              <button onClick={onDescend} style={{ display: "flex", alignItems: "center", gap: 5, background: "none", border: `1px solid ${C.steel}`, borderRadius: 5, color: C.steel, fontFamily: MONO, fontSize: 11, padding: "6px 12px", cursor: "pointer" }}>
                Enter <CornerDownLeft size={11} style={{ transform: "scaleY(-1)" }} />
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
