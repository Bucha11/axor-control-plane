// Taint / provenance graph panel (spec decision 6): the k-hop neighbourhood of a
// value ref, expand-on-click. Each edge is a derivation that happened in some
// run; clicking an edge jumps to that run's replay (edge → EvidenceCase). Nodes
// re-focus the graph on click. Attestations covering the focus are listed below —
// the same append-only surface the fact log shows.
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, GraphKhop } from "../api";
import { navigate } from "../router";
import { C, MONO } from "../theme";

// Deterministic circular layout: focus at centre, neighbours evenly on a ring.
// No physics sim — a taint neighbourhood is small (k-hop, capped) and a stable
// layout reads better than a jittering force graph.
function layout(g: GraphKhop, w: number, h: number) {
  const cx = w / 2;
  const cy = h / 2;
  const r = Math.min(w, h) / 2 - 40;
  const others = g.nodes.filter((n) => n !== g.focus);
  const pos = new Map<string, { x: number; y: number }>();
  pos.set(g.focus, { x: cx, y: cy });
  others.forEach((n, i) => {
    const a = (2 * Math.PI * i) / Math.max(others.length, 1) - Math.PI / 2;
    pos.set(n, { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) });
  });
  return pos;
}

export default function TaintGraph({ focus }: { focus: string }) {
  const [current, setCurrent] = useState(focus);
  const W = 460;
  const H = 300;

  const khop = useQuery({
    queryKey: ["khop", current],
    queryFn: () => api.graphKhop(current, 2, 60),
  });
  const atts = useQuery({
    queryKey: ["attestations", current],
    queryFn: () => api.graphAttestations(current),
  });

  const short = (ref: string) => (ref.length > 14 ? ref.slice(0, 12) + "…" : ref);

  return (
    <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
      <div style={{ fontFamily: MONO, fontSize: 12, color: C.text, marginBottom: 8 }}>
        provenance graph · focus <span style={{ color: C.steel }}>{short(current)}</span>
        {current !== focus && (
          <button
            onClick={() => setCurrent(focus)}
            style={{ marginLeft: 10, fontFamily: MONO, fontSize: 10.5, color: C.dim, background: "none", border: `1px solid ${C.line}`, borderRadius: 5, padding: "1px 7px", cursor: "pointer" }}
          >
            reset
          </button>
        )}
      </div>

      {khop.isPending ? (
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>loading…</div>
      ) : khop.isError ? (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{(khop.error as Error).message}</div>
      ) : khop.data && khop.data.nodes.length <= 1 && khop.data.edges.length === 0 ? (
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
          no recorded derivations touch this value — it is a graph of one.
        </div>
      ) : khop.data ? (
        (() => {
          const g = khop.data;
          const pos = layout(g, W, H);
          return (
            <svg width={W} height={H} style={{ maxWidth: "100%", display: "block" }}>
              {g.edges.map((e, i) => {
                const a = pos.get(e.src);
                const b = pos.get(e.dst);
                if (!a || !b) return null;
                const mx = (a.x + b.x) / 2;
                const my = (a.y + b.y) / 2;
                return (
                  <g key={i} style={{ cursor: "pointer" }} onClick={() => navigate(`replay/${e.run_id}`)}>
                    <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={C.line} strokeWidth={1.5} />
                    <title>{`derived in ${e.run_id} — click to open its replay`}</title>
                    <circle cx={mx} cy={my} r={2.5} fill={C.steel} />
                  </g>
                );
              })}
              {g.nodes.map((n) => {
                const p = pos.get(n)!;
                const isFocus = n === g.focus;
                return (
                  <g key={n} style={{ cursor: "pointer" }} onClick={() => setCurrent(n)}>
                    <circle cx={p.x} cy={p.y} r={isFocus ? 8 : 6} fill={isFocus ? C.steel : C.panel} stroke={isFocus ? C.steel : C.mut} strokeWidth={1.5} />
                    <text x={p.x} y={p.y - 12} textAnchor="middle" fontSize={9.5} fontFamily={MONO} fill={isFocus ? C.text : C.mut}>
                      {short(n)}
                    </text>
                    <title>{n} — click to re-focus</title>
                  </g>
                );
              })}
            </svg>
          );
        })()
      ) : null}

      <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 6 }}>
        click a node to expand · click an edge to open the run it was derived in
      </div>

      {atts.data && atts.data.length > 0 && (
        <div style={{ marginTop: 12, borderTop: `1px solid ${C.line}`, paddingTop: 10 }}>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.text, marginBottom: 6 }}>
            attestations covering this branch
          </div>
          {atts.data.map((a) => (
            <div key={a.fact_id} style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginBottom: 3 }}>
              <span style={{ color: a.revokes ? C.amber : C.green }}>{a.revokes ? "revokes" : "attests"}</span>
              {" · "}{a.operator} — {a.reason}
              {a.revokes && <span style={{ color: C.dim }}> (of {a.revokes})</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
