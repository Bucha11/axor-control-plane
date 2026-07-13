// The multi-agent half of the EvidenceCase render (spec v2 Ch.3; mockups/v2/
// evidencecase-multiagent.jsx). Derived on open from the trace — the subgraph
// is the minimal set of nodes that produced the claim, not the org chart.
//
// One format, two renders (decision v2-12/Ch.3 §7): this component renders
// NOTHING for a size-1 subgraph — the untouched v0.13 receipt is the whole
// case there. It only appears when the causes span more than one node.
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Shield } from "lucide-react";
import { api, CaseAnchor, SubgraphPayload } from "../api";
import { C, MONO } from "../theme";

const ROLE_COLOR: Record<string, string> = {
  origin: C.red,
  conduit: C.amber,
  container: C.green,
  anchor: C.red,
};

const ROLE_NOTE: Record<string, string> = {
  origin: "fault landed here",
  conduit: "propagated through — taint carried, not laundered",
  container: "denied propagation here",
  anchor: "claim reached a consequence here",
};

// Chain order: origin first, anchor last, following the message edges.
function orderNodes(sub: SubgraphPayload): string[] {
  const next = new Map(sub.edges.map((e) => [e.from, e.to]));
  const targets = new Set(sub.edges.map((e) => e.to));
  const starts = sub.nodes.map((n) => n.node_id).filter((id) => !targets.has(id));
  const ordered: string[] = [];
  const seen = new Set<string>();
  for (const s of starts) {
    let cur: string | undefined = s;
    let guard = 0;
    while (cur && !seen.has(cur) && guard++ < 32) {
      ordered.push(cur);
      seen.add(cur);
      cur = next.get(cur);
    }
  }
  for (const n of sub.nodes) if (!seen.has(n.node_id)) ordered.push(n.node_id);
  return ordered;
}

export default function CausalSubgraph({
  runId,
  anchor,
}: {
  runId: string;
  anchor: CaseAnchor;
}) {
  const [openRank, setOpenRank] = useState(false);
  const sub = useQuery({
    queryKey: ["subgraph", runId, anchor.node_id, anchor.seq],
    queryFn: () => api.subgraph(runId, anchor),
  });
  const rank = useQuery({
    queryKey: ["influence", runId, anchor.node_id, anchor.seq],
    queryFn: () => api.influence(runId, anchor, {}),
    enabled: openRank,
  });

  if (!sub.data || sub.data.nodes.length <= 1) return null; // size-1 → receipt only

  const s = sub.data;
  const order = orderNodes(s);
  const contained = (s.contained_at?.length ?? 0) > 0;
  const roleOf = (id: string) =>
    s.nodes.find((n) => n.node_id === id)?.roles ?? [];
  const Y = (i: number) => 55 + i * 85;
  const rolesShown = [...new Set(s.nodes.flatMap((n) => n.roles))];

  return (
    <div className="mt-3" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}
      data-testid="causal-subgraph">
      <div className="flex items-center justify-between px-4 py-2.5" style={{ borderBottom: `1px solid ${C.line}` }}>
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.08em" }}>
          CAUSAL SUBGRAPH · the {s.nodes.length} nodes that produced this claim
        </span>
        <span style={{ fontFamily: MONO, fontSize: 10, color: contained ? C.green : C.red, fontWeight: 700 }}>
          {contained ? "CONTAINED" : "ESCAPED"} · {s.federation_scope}
        </span>
      </div>

      <div className="flex" style={{ padding: "8px 4px" }}>
        <svg viewBox={`0 0 150 ${Y(order.length - 1) + 65}`} style={{ width: 150, flexShrink: 0 }}>
          {order.slice(0, -1).map((id, i) => (
            <g key={`e-${id}`}>
              <path d={`M 70 ${Y(i)} L 70 ${Y(i + 1)}`} stroke={C.amber}
                strokeWidth="1.5" strokeDasharray="4 3" />
              <text x={80} y={(Y(i) + Y(i + 1)) / 2} fill={C.amber} fontSize="7" fontFamily={MONO}>
                taint→
              </text>
            </g>
          ))}
          {/* the consequence edge below the anchor */}
          <path d={`M 70 ${Y(order.length - 1)} L 70 ${Y(order.length - 1) + 50}`}
            stroke={contained ? C.green : C.red} strokeWidth="1.5"
            strokeDasharray={contained ? "4 3" : "0"} />
          {contained ? (
            <>
              <circle cx="70" cy={Y(order.length - 1) + 40} r="9" fill={C.panel}
                stroke={C.green} strokeWidth="1.3" />
              <text x="70" y={Y(order.length - 1) + 43} textAnchor="middle" fontSize="9">🛡</text>
            </>
          ) : (
            <text x="82" y={Y(order.length - 1) + 44} fill={C.red} fontSize="7.5" fontFamily={MONO}>
              escaped
            </text>
          )}
          {order.map((id, i) => {
            const roles = roleOf(id);
            const col = ROLE_COLOR[roles.includes("container") ? "container" : roles[0] ?? "conduit"];
            return (
              <g key={id}>
                <circle cx="70" cy={Y(i)} r="14" fill={C.bg} stroke={col} strokeWidth="1.6"
                  style={{ filter: `drop-shadow(0 0 4px ${col})` }} />
                <text x="70" y={Y(i) - 20} textAnchor="middle" fill={C.text} fontSize="8.5" fontFamily={MONO}>
                  {id}
                </text>
                <text x="70" y={Y(i) + 27} textAnchor="middle" fill={col} fontSize="7.5" fontFamily={MONO}>
                  {roles.join(" · ")}
                </text>
              </g>
            );
          })}
        </svg>

        <div className="flex-1 flex flex-col justify-center gap-2.5 pr-3">
          {rolesShown.map((r) => (
            <div key={r} className="flex items-start gap-2">
              <span style={{ width: 8, height: 8, borderRadius: 4, background: ROLE_COLOR[r], marginTop: 3, flexShrink: 0 }} />
              <div>
                <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, fontWeight: 600 }}>{r}</span>
                <div style={{ fontFamily: MONO, fontSize: 10, color: C.mut, lineHeight: 1.4 }}>{ROLE_NOTE[r]}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* influence ranking, folded (subgraph ablation — deterministic) */}
      <div style={{ borderTop: `1px solid ${C.line}` }}>
        <button onClick={() => setOpenRank(!openRank)} className="flex items-center gap-2 px-4 py-2.5 w-full"
          style={{ background: "none", border: "none", color: C.mut, fontFamily: MONO, fontSize: 11, cursor: "pointer" }}>
          {openRank ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          influence ranking — which upstream value most drove the anchor's claim
        </button>
        {openRank && (
          <div className="px-4 pb-3" style={{ paddingLeft: 34 }}>
            {(rank.data?.ranking ?? []).map((r) => (
              <div key={r.ref} className="flex items-center gap-3 py-1">
                <span style={{ fontFamily: MONO, fontSize: 11, color: C.text, flex: 1 }}>{r.ref}</span>
                <span style={{ width: 80, height: 4, background: C.bg, borderRadius: 2, overflow: "hidden" }}>
                  <span style={{ display: "block", width: `${r.influence * 100}%`, height: "100%", background: r.influence > 0.5 ? C.red : C.amber }} />
                </span>
                <span style={{ fontFamily: MONO, fontSize: 10, color: C.mut, width: 34 }}>{r.influence.toFixed(2)}</span>
              </div>
            ))}
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, marginTop: 6 }}>
              ranked by subgraph ablation — deterministic, bounded by causal-chain length
            </div>
          </div>
        )}
      </div>

      <div className="px-4 py-2" style={{ borderTop: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 10, color: C.dim }}>
        {contained
          ? "one case, one consequence — the fabrication propagated but was contained at the boundary; intermediate fabrications are conduit nodes in this case, not separate cases"
          : "one case, one consequence — the discrepancy escaped; the subgraph shows every node that fed it"}
        {" · "}
        <Shield size={9} style={{ display: "inline" }} /> subgraph derived from the trace on open, never stored
      </div>
    </div>
  );
}
