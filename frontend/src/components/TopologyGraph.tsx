// The graph lens — Control's second mode (spec v2 Ch.1 §4; mockups/v2/
// federation-topology.jsx). The one view a list cannot carry: edges, edge
// kinds, and the federation boundary. Hand-rolled SVG (house pattern, no d3);
// deterministic tiered layout — depth from traced delegation edges, never
// from self-report.
//
// Foreign peers render as opaque diamonds with ZERO intervention affordances —
// structurally absent, not greyed (spec v2 Ch.1 §2): their internals are not
// ours to steer.
import { Lock } from "lucide-react";
import { TopologyEdge, TopologyNode, TopologyPayload } from "../api";
import { C, MONO } from "../theme";

const LEVEL_COLOR: Record<string, string> = {
  NORMAL: C.green,
  CAUTIOUS: C.amber,
  RESTRICTED: C.red,
  LOCKED: C.red,
  TERMINAL: C.red,
};

interface Pos {
  x: number;
  y: number;
}

// Depth = longest delegation chain from a root (a node never seen as a
// delegation target). Peers sit outside the enclosure on the right.
interface Layout {
  pos: Map<string, Pos>;
  // Canvas and enclosure grow with the widest row and the peer column, so a
  // fifth sibling or a fourth peer is drawn, not clipped.
  width: number;
  height: number;
  rowWidth: number;
}

function layout(payload: TopologyPayload): Layout {
  const selfNodes = payload.nodes.filter((n) => n.kind === "self");
  const peers = payload.nodes.filter((n) => n.kind === "peer");
  const childOf = new Map<string, string>();
  for (const e of payload.edges) {
    if (e.kind === "delegation") childOf.set(e.to, e.from);
  }
  const depth = (id: string): number => {
    let d = 0;
    let cur = childOf.get(id);
    let guard = 0;
    while (cur && guard++ < 32) {
      d += 1;
      cur = childOf.get(cur);
    }
    return d;
  };
  const rows = new Map<number, string[]>();
  for (const n of selfNodes) {
    const d = depth(n.node_id);
    rows.set(d, [...(rows.get(d) ?? []), n.node_id]);
  }
  const pos = new Map<string, Pos>();
  const maxDepth = Math.max(0, ...rows.keys());
  const widest = Math.max(1, ...[...rows.values()].map((ids) => ids.length));
  // ~70 units per node keeps labels from overlapping; 260 is the old minimum.
  const rowWidth = Math.max(260, (widest + 1) * 70);
  for (const [d, ids] of rows) {
    ids.sort();
    ids.forEach((id, i) => {
      pos.set(id, {
        x: 60 + ((i + 1) * rowWidth) / (ids.length + 1),
        y: 70 + (maxDepth === 0 ? 0 : (d * 220) / Math.max(1, maxDepth)),
      });
    });
  }
  const peerX = 40 + rowWidth + 30 + 70;
  peers.forEach((p, i) => pos.set(p.node_id, { x: peerX, y: 100 + i * 90 }));
  const height = Math.max(340, 100 + Math.max(0, peers.length - 1) * 90 + 70);
  const width = peers.length ? peerX + 80 : 40 + rowWidth + 30 + 150;
  return { pos, width, height, rowWidth };
}

export default function TopologyGraph({
  payload,
  selected,
  onSelect,
}: {
  payload: TopologyPayload;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const { pos, width, height, rowWidth } = layout(payload);
  const hasPeers = payload.nodes.some((n) => n.kind === "peer");
  const hasReputation = payload.nodes.some(
    (n) => n.reputation?.flagged || n.reputation?.watch,
  );

  const edgePath = (e: TopologyEdge): string => {
    const a = pos.get(e.from);
    const b = pos.get(e.to);
    if (!a || !b) return "";
    return `M ${a.x} ${a.y} L ${b.x} ${b.y}`;
  };

  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: "hidden" }}>
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: "100%", display: "block" }} data-testid="topology-graph">
        {/* federation enclosure — the keyset boundary (spec v2 Ch.1) */}
        <rect x="40" y="30" width={rowWidth + 30} height={height - 50} rx="14"
          fill="rgba(127,168,204,0.03)" stroke={C.steel} strokeWidth="1" strokeDasharray="2 4" />
        <text x="52" y="50" fill={C.steel} fontSize="9" fontFamily={MONO} opacity="0.7">
          FEDERATION · your keyset
        </text>

        {payload.edges.map((e) => {
          const inter = e.kind === "peer";
          const lateral = e.kind === "lateral";
          const flashed = e.denied > 0;
          return (
            <g key={`${e.from}-${e.to}-${e.kind}`}>
              {inter ? (
                <path d={edgePath(e)} stroke={flashed ? C.red : C.violet}
                  strokeWidth="2.5" fill="none" opacity="0.4" />
              ) : (
                <path d={edgePath(e)}
                  stroke={flashed ? C.red : lateral ? C.dim : C.line}
                  strokeWidth="1.5" strokeDasharray={lateral ? "4 3" : "0"} fill="none" />
              )}
              {flashed && (() => {
                const a = pos.get(e.from);
                const b = pos.get(e.to);
                if (!a || !b) return null;
                return (
                  <text x={(a.x + b.x) / 2 + 8} y={(a.y + b.y) / 2} fill={C.red}
                    fontSize="8" fontFamily={MONO}>
                    DENIED{e.last_gate ? ` · ${e.last_gate}` : ""}
                  </text>
                );
              })()}
            </g>
          );
        })}

        {payload.nodes.map((n) => {
          const p = pos.get(n.node_id);
          if (!p) return null;
          const isPeer = n.kind === "peer";
          const level = n.reported?.level ?? "NORMAL";
          const ring = isPeer ? C.violet : (LEVEL_COLOR[level] ?? C.mut);
          const hot = !isPeer && level !== "NORMAL";
          return (
            <g key={n.node_id} onClick={() => onSelect(n.node_id)} style={{ cursor: "pointer" }}
              data-testid={`topo-node-${n.node_id}`}>
              {isPeer ? (
                <rect x={p.x - 15} y={p.y - 15} width="30" height="30" rx="4"
                  fill={C.bg} stroke={ring} strokeWidth="1.5"
                  transform={`rotate(45 ${p.x} ${p.y})`} />
              ) : (
                <circle cx={p.x} cy={p.y} r="17"
                  fill={selected === n.node_id ? "rgba(127,168,204,0.12)" : C.bg}
                  stroke={ring} strokeWidth={hot ? "2" : "1.5"}
                  style={hot ? { filter: `drop-shadow(0 0 5px ${ring})` } : {}} />
              )}
              {isPeer && <Lock x={p.x - 5} y={p.y - 5} width={10} height={10} color={C.violet} />}
              <text x={p.x} y={p.y + 30} textAnchor="middle"
                fill={selected === n.node_id ? C.text : C.mut} fontSize="9.5" fontFamily={MONO}>
                {n.node_id}
              </text>
              {!isPeer && level !== "NORMAL" && (
                <text x={p.x} y={p.y + 41} textAnchor="middle"
                  fill={LEVEL_COLOR[level] ?? C.mut} fontSize="8" fontFamily={MONO}>
                  {level}
                </text>
              )}
              {isPeer && (
                <text x={p.x} y={p.y + 41} textAnchor="middle" fill={C.steel}
                  fontSize="8" fontFamily={MONO}>
                  peer · opaque
                </text>
              )}
              {/* Cross-session reputation (ui-spec:416), from the node's OWN
                  axor-sentinel. The badge only appears where a sentinel is
                  reporting AND it found something: absence of a badge means
                  "no cross-session verdict here", which is deliberately not
                  drawn as a green one — an unwatched node must not read as the
                  safest thing on the graph. The number is a count of flagged
                  resources, and the panel below names them. */}
              {!isPeer && (n.reputation?.flagged || n.reputation?.watch) ? (
                <g data-testid={`topo-rep-${n.node_id}`}>
                  <circle cx={p.x + 14} cy={p.y - 14} r="7.5" fill={C.bg}
                    stroke={n.reputation.flagged ? C.red : C.amber} strokeWidth="1.5" />
                  <text x={p.x + 14} y={p.y - 11} textAnchor="middle" fontSize="8"
                    fontFamily={MONO} fontWeight={700}
                    fill={n.reputation.flagged ? C.red : C.amber}>
                    {n.reputation.flagged || n.reputation.watch}
                  </text>
                </g>
              ) : null}
            </g>
          );
        })}
      </svg>

      <div className="flex items-center gap-4 px-4 py-2"
        style={{ borderTop: `1px solid ${C.line}`, fontFamily: MONO, fontSize: 9.5, color: C.dim }}>
        <span>solid = delegation</span>
        <span>dashed = lateral (intra)</span>
        {hasPeers && <span style={{ color: C.violet }}>violet = inter-federation</span>}
        {hasReputation && (
          <span style={{ color: C.red }}>
            corner badge = resources axor-sentinel flagged across sessions
          </span>
        )}
        <span style={{ marginLeft: "auto" }}>red edge = denied at the boundary</span>
      </div>
    </div>
  );
}

export function PeerCard({ node }: { node: TopologyNode }) {
  // Zero intervention affordances by construction: no pause, no inject,
  // no attest — the card explains why instead of greying buttons out.
  return (
    <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
      <div className="flex items-center gap-2 mb-2">
        <Lock size={13} color={C.violet} />
        <span style={{ fontFamily: MONO, fontSize: 13, color: C.text }}>{node.node_id}</span>
        <span style={{ fontFamily: MONO, fontSize: 10, color: C.steel }}>foreign federation</span>
      </div>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
        Different keyset — opaque by design. We govern <span style={{ color: C.text }}>our edge to it</span>,
        not its internals: inbound values are re-derived per its declared trust level, and our
        node's sends to it are gated like exports. No pause / inject / attest — structurally,
        not greyed: their agent is not ours to steer.
      </div>
    </div>
  );
}
