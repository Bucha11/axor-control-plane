// Value provenance panel (spec decision 6): the k-hop neighbourhood of a value
// ref INSIDE ONE RUN, expand-on-click. Nodes re-focus on click. Attestations
// covering the focus are listed below — the same append-only surface the fact
// log shows, with revoked coverage struck through rather than removed.
//
// The run is not a filter, it is the scope. Value refs are minted per trace from
// a counter that restarts at zero, so `v_ext_1` names a different value in every
// run; the cross-run graph this panel used to draw merged unrelated values and
// showed one run's edges under another's ref.
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, GraphKhop } from "../api";
import { C, MONO } from "../theme";
import Tooltip from "../components/Tooltip";

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

export default function TaintGraph({ runId, focus }: { runId: string; focus: string }) {
  const [current, setCurrent] = useState(focus);
  const W = 460;
  const H = 300;

  const khop = useQuery({
    queryKey: ["provenance", runId, current],
    queryFn: () => api.runProvenance(runId, current, 2, 60),
  });
  const atts = useQuery({
    queryKey: ["attestations", runId, current],
    queryFn: () => api.runAttestations(runId, current),
  });

  // Attest THIS branch: an append-only operator_attestation fact whose covers[]
  // names the focus value ref and whose run_id says which run's ref that is —
  // without the run the coverage would land on every other run's ref of the
  // same name, and the plane refuses it.
  const attest = useMutation({
    mutationFn: (reason: string) =>
      api.appendFact("operator", {
        fact_id: `att_${runId}_${current}_${Date.now()}`,
        fact_type: "operator_attestation",
        reason,
        operator: "op_ui",
        run_id: runId,
        covers: [current],
      }),
    onSuccess: () => void atts.refetch(),
  });

  const short = (ref: string) => (ref.length > 14 ? ref.slice(0, 12) + "…" : ref);

  return (
    <div className="mt-3 p-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
      <div style={{ fontFamily: MONO, fontSize: 12, color: C.text, marginBottom: 8 }}>
        provenance · run <span style={{ color: C.steel }}>{runId}</span> · focus{" "}
        <span style={{ color: C.steel }}>{short(current)}</span>
        {current !== focus && (
          <button
            onClick={() => setCurrent(focus)}
            style={{ marginLeft: 10, fontFamily: MONO, fontSize: 10.5, color: C.dim, background: "none", border: `1px solid ${C.line}`, borderRadius: 5, padding: "1px 7px", cursor: "pointer" }}
          >
            reset
          </button>
        )}
        <Tooltip content="Vouch for this value's branch after you've checked it — an append-only reputation event with a required reason. It lowers the branch's suspicion; it never resets or erases history.">
          <button
            onClick={() => {
              const reason = window.prompt(`Attest branch ${short(current)} — reason (required, append-only):`);
              if (reason && reason.trim()) attest.mutate(reason.trim());
            }}
            disabled={attest.isPending}
            style={{ marginLeft: 10, fontFamily: MONO, fontSize: 10.5, color: C.steel, background: "none", border: `1px solid ${C.line}`, borderRadius: 5, padding: "1px 7px", cursor: "pointer" }}
          >
            {attest.isPending ? "attesting…" : "attest branch"}
          </button>
        </Tooltip>
      </div>

      {khop.isPending ? (
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>loading…</div>
      ) : khop.isError ? (
        <div style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{(khop.error as Error).message}</div>
      ) : khop.data && khop.data.nodes.length <= 1 && khop.data.edges.length === 0 ? (
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
          no derivation in this run touches this value.
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
                  <g key={i}>
                    <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={C.line} strokeWidth={1.5} />
                    <title>{`${e.src} → ${e.dst}`}</title>
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
        click a node to expand · derivations recorded in this run
      </div>

      {atts.data && atts.data.length > 0 && (
        <div style={{ marginTop: 12, borderTop: `1px solid ${C.line}`, paddingTop: 10 }}>
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.text, marginBottom: 6 }}>
            attestations covering this branch
          </div>
          {atts.data.map((a) => (
            <div
              key={a.fact_id}
              style={{
                fontFamily: MONO, fontSize: 10.5, marginBottom: 3,
                color: a.in_effect || a.revokes ? C.mut : C.dim,
                textDecoration: a.revokes || a.in_effect ? "none" : "line-through",
              }}
            >
              <span style={{ color: a.revokes ? C.amber : a.in_effect ? C.green : C.dim }}>
                {a.revokes ? "revokes" : a.in_effect ? "attests" : "revoked"}
              </span>
              {" · "}{a.operator} — {a.reason}
              {a.revokes && <span style={{ color: C.dim }}> (of {a.revokes})</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
