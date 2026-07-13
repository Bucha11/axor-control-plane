// The two-tree containment view — the multi-agent hero (spec v2 Ch.2 §3,
// decision v2-18; mockups/v2/two-tree-containment.jsx): the same recorded
// fault replayed over both topologies, aligned on the fault-injection point.
// Left: ungoverned — the lie reaches the export. Right: governed — denied at
// the boundary. Both trees are re-derived from ONE recorded trace
// (deterministic replay); the divergence is real data, not two runs.
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Play, RotateCcw, Shield } from "lucide-react";
import { api, CaseAnchor } from "../api";
import { C, MONO } from "../theme";

function useChain(runId: string, anchor: CaseAnchor) {
  const sub = useQuery({
    queryKey: ["subgraph", runId, anchor.node_id, anchor.seq],
    queryFn: () => api.subgraph(runId, anchor),
  });
  const cont = useQuery({
    queryKey: ["containment", runId, anchor.node_id, anchor.seq],
    queryFn: () => api.containment(runId, anchor),
  });
  return { sub: sub.data, cont: cont.data };
}

export default function TwoTreeContainment({
  runId,
  anchor,
}: {
  runId: string;
  anchor: CaseAnchor;
}) {
  const { sub, cont } = useChain(runId, anchor);
  const [step, setStep] = useState(-1); // -1 idle; then 0..chain steps; last = verdict
  const [playing, setPlaying] = useState(false);

  const chain: string[] = (() => {
    if (!sub) return [];
    const next = new Map(sub.edges.map((e) => [e.from, e.to]));
    const targets = new Set(sub.edges.map((e) => e.to));
    const start = sub.nodes.map((n) => n.node_id).find((id) => !targets.has(id));
    const out: string[] = [];
    let cur = start;
    let guard = 0;
    while (cur && guard++ < 32) {
      out.push(cur);
      cur = next.get(cur);
    }
    return out;
  })();

  const steps = chain.length + 2; // fault, each hop, verdict
  useEffect(() => {
    if (!playing || step >= steps - 1) return;
    const t = setTimeout(() => setStep((x) => x + 1), step < 0 ? 250 : 1100);
    return () => clearTimeout(t);
  }, [playing, step, steps]);

  if (!sub || !cont || sub.nodes.length <= 1) return null;

  const verdict = step >= steps - 1;
  const captions = [
    `fault injected at the leaf: ${chain[0] ?? "origin"}`,
    ...chain.slice(0, -1).map((id, i) => `the fabrication (tainted) travels ${id} → ${chain[i + 1]}…`),
    "…and reaches the export boundary",
  ];

  const Tree = ({ governed }: { governed: boolean }) => {
    const contained = verdict && governed;
    const escapedV = verdict && !governed;
    const Y = (i: number) => 40 + i * 70;
    return (
      <div style={{ flex: 1, background: C.panel2, border: `1px solid ${governed ? (contained ? C.green : C.line) : escapedV ? C.red : C.line}`, borderRadius: 10, overflow: "hidden" }}>
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
        <svg viewBox={`0 0 140 ${Y(chain.length - 1) + 55}`} style={{ width: "100%", display: "block" }}>
          {chain.slice(0, -1).map((id, i) => (
            <line key={id} x1="70" y1={Y(i)} x2="70" y2={Y(i + 1)}
              stroke={step > i ? C.amber : C.line} strokeWidth="1.5" />
          ))}
          {/* export edge from the last node */}
          <line x1="70" y1={Y(chain.length - 1)} x2="70" y2={Y(chain.length - 1) + 42}
            stroke={step >= chain.length ? (contained ? C.green : C.red) : C.line}
            strokeWidth="1.5" strokeDasharray={contained ? "4 3" : "0"} />
          {contained && (
            <g>
              <circle cx="70" cy={Y(chain.length - 1) + 32} r="10" fill={C.panel2} stroke={C.green} strokeWidth="1.5" />
              <text x="70" y={Y(chain.length - 1) + 36} textAnchor="middle" fontSize="10">🛡</text>
            </g>
          )}
          {escapedV && (
            <text x="70" y={Y(chain.length - 1) + 38} textAnchor="middle" fill={C.red} fontSize="8" fontFamily={MONO} fontWeight="700">
              → EXPORT
            </text>
          )}
          {chain.map((id, i) => {
            const touched = step >= i;
            let ring: string = C.line;
            if (touched) ring = governed && i === chain.length - 1 && verdict ? C.green : governed ? C.amber : C.red;
            return (
              <g key={id}>
                <circle cx="70" cy={Y(i)} r="15" fill={C.panel2} stroke={ring} strokeWidth="1.5"
                  style={touched && !governed ? { filter: `drop-shadow(0 0 5px ${ring})` } : {}} />
                <text x="92" y={Y(i) + 4} fill={C.mut} fontSize="8.5" fontFamily={MONO}>{id}</text>
              </g>
            );
          })}
        </svg>
      </div>
    );
  };

  return (
    <div className="mt-4" data-testid="two-tree">
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 8 }}>
        TWO-TREE CONTAINMENT — same recorded fault, both worlds · deterministic, no live model
      </div>
      <div className="flex gap-3">
        <Tree governed={false} />
        <Tree governed={true} />
      </div>

      <div className="mt-3 px-4 py-3 flex items-center justify-between" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, minHeight: 48 }}>
        {!playing ? (
          <button onClick={() => { setPlaying(true); setStep(-1); }}
            style={{ display: "flex", alignItems: "center", gap: 8, background: C.steel, border: "none", borderRadius: 5, color: C.bg, fontFamily: MONO, fontSize: 12, fontWeight: 700, padding: "7px 16px", cursor: "pointer" }}>
            <Play size={13} /> Run the recording
          </button>
        ) : verdict ? (
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.text }}>
            Same fabrication in both. Left: it reached the export. Right:{" "}
            <span style={{ color: C.green }}>denied at the boundary</span> — the agent failed honestly instead of lying.
          </span>
        ) : (
          <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
            {captions[Math.max(0, Math.min(step, captions.length - 1))]}
          </span>
        )}
        {playing && (
          <button onClick={() => setStep(-1)} style={{ background: "none", border: "none", color: C.dim, cursor: "pointer" }}>
            <RotateCcw size={14} />
          </button>
        )}
      </div>

      {/* containment table + systemic outcome — event-grounded (pure-A), the
          outcome as a LABEL pair, never a governance-attributed number */}
      <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10 }}>
        <div className="flex items-center justify-between mb-3">
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em" }}>
            CONTAINMENT — one fault, measured at each boundary
          </span>
          {cont.containment && (
            <span style={{ fontFamily: MONO, fontSize: 11, color: cont.held === cont.reached ? C.green : C.red, fontWeight: 700 }}>
              {cont.containment} boundaries held
            </span>
          )}
        </div>
        <div className="flex flex-col gap-1.5">
          {cont.rows.map((r: { edge: string; note: string; status: string }) => (
            <div key={`${r.edge}-${r.status}`} className="flex items-center justify-between">
              <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>{r.edge}</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: r.status === "held" ? C.green : r.status === "escaped" ? C.red : C.amber }}>
                {r.note}
              </span>
            </div>
          ))}
        </div>
        <div className="mt-3 pt-3" style={{ borderTop: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 11, color: C.mut }}>
          systemic outcome:{" "}
          <span style={{ color: C.red }}>{cont.ungoverned_outcome}</span>
          {" → "}
          <span style={{ color: C.green }}>{cont.governed_outcome}</span>
          <span style={{ color: C.dim }}> · a label, not a score — an honest non-answer, not a manufactured success</span>
        </div>
      </div>
    </div>
  );
}
