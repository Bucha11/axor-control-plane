// Sentinel — what a node's axor-sentinel found ACROSS sessions, as a graph.
//
// Sentinel exists to catch what no single session shows: a resource read a
// little at a time in many tainted sessions, then exported. That is a shape —
// resources joined by the sessions that touched them — and until now the plane
// showed it as one count on a node ("1 flagged") and a list of fact strings.
//
// The graph here is REBUILT from the facts behind each verdict. Sentinel posts
// levels and those facts, not its full graph, so an edge exists exactly where a
// fact names a session (P1, P3, P4, W1). A clean resource has no facts, hence
// no edges — it is drawn, unconnected, rather than left out.
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Radar } from "lucide-react";
import { api, type ReputationSnapshot } from "../api";
import { C, MONO, btn } from "../theme";
import { useRoute } from "../router";

type Level = "CLEAN" | "WATCH" | "FLAGGED";
type EdgeKind = "export_denied" | "staging" | "read" | "export" | "touched";

interface Link { resource: string; session: string; kind: EdgeKind; heuristic: boolean }

const LEVEL_COLOR: Record<Level, string> = { FLAGGED: C.red, WATCH: C.amber, CLEAN: C.green };
const EDGE_STYLE: Record<EdgeKind, { color: string; dash?: string; label: string }> = {
  export_denied: { color: C.red, label: "export denied" },
  export: { color: C.red, dash: "5 4", label: "exported" },
  staging: { color: C.amber, label: "staged (tainted)" },
  read: { color: C.steel, label: "read while tainted" },
  touched: { color: C.amber, dash: "3 4", label: "touched (tainted)" },
};

function sessionsIn(text: string): string[] {
  return Array.from(text.matchAll(/'([^']+)'/g), (m) => m[1]);
}

/** Facts → links, and the same facts in words. Formats are axor-sentinel's
 * predicates.py: P1/P3/W1 list sessions, P4 names a read and an export,
 * P2 lists origins, C1/C2 are about containers, `heuristic_ceiling:` wraps a
 * predicate that fired on a guessed resource id. */
export function readFacts(resource: string, facts: string[]): { links: Link[]; lines: string[] } {
  const links: Link[] = [];
  const lines: string[] = [];
  for (const raw of facts) {
    const heuristic = raw.startsWith("heuristic_ceiling:");
    const fact = heuristic ? raw.slice("heuristic_ceiling:".length) : raw;
    const caveat = heuristic ? " (resource id was inferred, so this can only reach WATCH)" : "";
    if (fact.startsWith("P1:")) {
      const s = sessionsIn(fact);
      s.forEach((session) => links.push({ resource, session, kind: "export_denied", heuristic }));
      lines.push(`an export of it was denied at the sink in ${s.join(", ")}${caveat}`);
    } else if (fact.startsWith("P3:")) {
      const s = sessionsIn(fact);
      s.forEach((session) => links.push({ resource, session, kind: "staging", heuristic }));
      lines.push(`staged in ${s.length} tainted sessions inside the window: ${s.join(", ")}${caveat}`);
    } else if (fact.startsWith("P4:")) {
      const m = fact.match(/read=([^:]+):export=(.+)$/);
      if (m) {
        links.push({ resource, session: m[1], kind: "read", heuristic });
        links.push({ resource, session: m[2], kind: "export", heuristic });
        lines.push(`read while tainted in ${m[1]}, then exported in a later session, ${m[2]} — staged, then sent${caveat}`);
      }
    } else if (fact.startsWith("W1:")) {
      const s = sessionsIn(fact);
      s.forEach((session) => links.push({ resource, session, kind: "touched", heuristic }));
      lines.push(`touched by tainted sessions ${s.join(", ")} — not enough to flag, enough to watch`);
    } else if (fact.startsWith("P2:")) {
      lines.push(`export-adjacent from several origins: ${sessionsIn(fact).join(", ")}${caveat}`);
    } else if (fact.startsWith("C1:")) {
      lines.push(`flagged members: ${fact.split("=")[1]}`);
    } else if (fact.startsWith("C2:")) {
      lines.push("a member is above clean");
    } else {
      lines.push(raw);
    }
  }
  return { links, lines };
}

const ORDER: Record<Level, number> = { FLAGGED: 0, WATCH: 1, CLEAN: 2 };

function Graph({ snap, selected, onSelect }: {
  snap: ReputationSnapshot; selected: string | null; onSelect: (r: string | null) => void;
}) {
  const resources = Object.keys(snap.resource_level)
    .sort((a, b) => ORDER[snap.resource_level[a]] - ORDER[snap.resource_level[b]] || a.localeCompare(b));
  const links = resources.flatMap((r) => readFacts(r, snap.verdict_facts[r] ?? []).links);
  const sessions = Array.from(new Set(links.map((l) => l.session))).sort();
  const rowH = 46;
  const height = Math.max(resources.length, sessions.length, 1) * rowH + 30;
  const rY = (i: number) => 30 + i * rowH + (Math.max(0, sessions.length - resources.length) * rowH) / 2;
  const sY = (i: number) => 30 + i * rowH + (Math.max(0, resources.length - sessions.length) * rowH) / 2;
  const RX = 170;
  const SX = 470;
  const related = selected
    ? new Set(links.filter((l) => l.resource === selected).map((l) => l.session))
    : null;

  return (
    <svg viewBox={`0 0 600 ${height}`} style={{ width: "100%", display: "block" }} role="img"
      aria-label="sentinel graph: resources and the sessions that touched them">
      <text x={RX} y={14} textAnchor="end" fill={C.dim} fontSize="9.5" fontFamily={MONO} letterSpacing="1">RESOURCES</text>
      <text x={SX} y={14} fill={C.dim} fontSize="9.5" fontFamily={MONO} letterSpacing="1">SESSIONS</text>
      {links.map((l, i) => {
        const y1 = rY(resources.indexOf(l.resource));
        const y2 = sY(sessions.indexOf(l.session));
        const st = EDGE_STYLE[l.kind];
        const dim = selected !== null && l.resource !== selected;
        return (
          <path key={i} d={`M ${RX + 10} ${y1} C ${(RX + SX) / 2} ${y1}, ${(RX + SX) / 2} ${y2}, ${SX - 10} ${y2}`}
            fill="none" stroke={st.color} strokeWidth={dim ? 1 : 1.8} strokeDasharray={st.dash}
            opacity={dim ? 0.15 : 0.9} />
        );
      })}
      {resources.map((r, i) => {
        const level = snap.resource_level[r];
        const on = selected === r;
        return (
          <g key={r} style={{ cursor: "pointer" }} onClick={() => onSelect(on ? null : r)}
            data-testid={`sentinel-resource-${r}`}>
            <circle cx={RX} cy={rY(i)} r={9} fill={C.panel} stroke={LEVEL_COLOR[level]} strokeWidth={on ? 3 : 2}
              style={level !== "CLEAN" ? { filter: `drop-shadow(0 0 5px ${LEVEL_COLOR[level]})` } : {}} />
            <text x={RX - 16} y={rY(i) + 4} textAnchor="end" fill={on ? C.text : C.mut} fontSize="11.5" fontFamily={MONO}>{r}</text>
            <text x={RX - 16} y={rY(i) + 17} textAnchor="end" fill={LEVEL_COLOR[level]} fontSize="9" fontFamily={MONO}>{level.toLowerCase()}</text>
          </g>
        );
      })}
      {sessions.map((s, i) => (
        <g key={s} opacity={related && !related.has(s) ? 0.3 : 1}>
          <rect x={SX - 8} y={sY(i) - 8} width={16} height={16} rx={3} fill={C.panel} stroke={C.line} />
          <text x={SX + 16} y={sY(i) + 4} fill={C.mut} fontSize="11.5" fontFamily={MONO}>{s}</text>
        </g>
      ))}
    </svg>
  );
}

export default function Sentinel() {
  const qc = useQueryClient();
  const topo = useQuery({ queryKey: ["topology"], queryFn: api.topology });
  const watched = (topo.data?.nodes ?? []).filter((n) => n.reputation);
  const route = useRoute();
  // `#/sentinel?node=X` (the Agents list links here) opens on that node.
  const [node, setNode] = useState<string | null>(route.query.node || null);
  useEffect(() => {
    if (route.query.node) setNode(route.query.node);
  }, [route.query.node]);
  useEffect(() => {
    if (node === null && watched.length > 0) {
      // the node with the most to say first
      const best = [...watched].sort((a, b) => (b.reputation!.flagged - a.reputation!.flagged)
        || (b.reputation!.watch - a.reputation!.watch))[0];
      setNode(best.node_id);
    }
  }, [node, watched]);
  const rep = useQuery({
    queryKey: ["reputation", node],
    queryFn: () => api.reputation(node!),
    enabled: node !== null,
  });
  const [selected, setSelected] = useState<string | null>(null);
  const seed = useMutation({
    mutationFn: api.seedFleet,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["topology"] });
      void qc.invalidateQueries({ queryKey: ["nodes"] });
    },
  });

  const snap = rep.data?.reputation ?? null;
  // flagged first, so the default selection is the one worth reading
  const firstFlagged = useMemo(() => {
    if (!snap) return null;
    return Object.keys(snap.resource_level).find((r) => snap.resource_level[r] === "FLAGGED") ?? null;
  }, [snap]);
  useEffect(() => { setSelected(firstFlagged); }, [firstFlagged]);

  const facts = selected && snap ? readFacts(selected, snap.verdict_facts[selected] ?? []).lines : [];

  return (
    <div style={{ maxWidth: 680, margin: "0 auto" }}>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>What sentinel sees across sessions</h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, marginBottom: 18, lineHeight: 1.6 }}>
        one session reading a customer table is normal. the same table staged a little in
        many tainted sessions, then exported, is a leak no single session shows. sentinel
        watches resources across all of them and marks them clean, watch or flagged.
      </div>

      {topo.isSuccess && watched.length === 0 && (
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, padding: 18 }}
          data-testid="sentinel-empty">
          <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.text, marginBottom: 6 }}>
            No node is reporting sentinel reputation yet.
          </div>
          <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim, lineHeight: 1.6, marginBottom: 14 }}>
            that is an absence of evidence, not a clean bill. axor-sentinel runs beside
            axor-core on the node and posts here. to see what it looks like:
          </div>
          <button onClick={() => seed.mutate()} disabled={seed.isPending}
            style={btn({ color: C.steel, borderColor: C.steel, fontSize: 12, padding: "7px 14px" })}>
            <Radar size={13} /> {seed.isPending ? "loading…" : "Load an example fleet"}
          </button>
          {seed.isError && <div style={{ color: C.red, fontFamily: MONO, fontSize: 11, marginTop: 8 }}>{(seed.error as Error).message}</div>}
        </div>
      )}

      {watched.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap mb-3" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          node
          <select value={node ?? ""} onChange={(e) => { setNode(e.target.value); setSelected(null); }}
            aria-label="sentinel node"
            style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "4px 8px" }}>
            {watched.map((n) => <option key={n.node_id} value={n.node_id}>{n.node_id}</option>)}
          </select>
          {snap && (
            <span style={{ color: C.dim }}>
              snapshot v{snap.version} · {new Date(snap.generated_at * 1000).toLocaleString()}
            </span>
          )}
        </div>
      )}

      {snap && (
        <>
          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, padding: "8px 6px" }}
            data-testid="sentinel-graph">
            <Graph snap={snap} selected={selected} onSelect={setSelected} />
          </div>
          <div className="flex flex-wrap" style={{ gap: "6px 14px", marginTop: 8, fontFamily: MONO, fontSize: 10, color: C.dim }}>
            {(Object.keys(EDGE_STYLE) as EdgeKind[]).map((k) => (
              <span key={k} className="flex items-center gap-1">
                <svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke={EDGE_STYLE[k].color} strokeWidth="2" strokeDasharray={EDGE_STYLE[k].dash} /></svg>
                {EDGE_STYLE[k].label}
              </span>
            ))}
            <span>· click a resource to read why</span>
          </div>

          {selected && (
            <div data-testid="sentinel-why" style={{ marginTop: 14, background: C.panel, border: `1px solid ${C.line}`,
              borderLeft: `3px solid ${LEVEL_COLOR[snap.resource_level[selected]]}`, borderRadius: 8, padding: "12px 14px" }}>
              <div style={{ fontFamily: MONO, fontSize: 12.5, color: C.text, marginBottom: 6 }}>
                {selected} · <span style={{ color: LEVEL_COLOR[snap.resource_level[selected]] }}>
                  {snap.resource_level[selected].toLowerCase()}</span>
              </div>
              {facts.length === 0 ? (
                <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.dim }}>no predicate fired — nothing to explain.</div>
              ) : (
                <ul style={{ margin: 0, paddingLeft: 18, fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7 }}>
                  {facts.map((f, i) => <li key={i}>{f}</li>)}
                </ul>
              )}
            </div>
          )}

          {Object.keys(snap.container_level ?? {}).length > 0 && (
            <div style={{ marginTop: 14, fontFamily: MONO, fontSize: 11, color: C.mut, lineHeight: 1.7 }}>
              <div style={{ fontSize: 9.5, color: C.dim, letterSpacing: "0.1em", marginBottom: 4 }}>CONTAINERS</div>
              {Object.entries(snap.container_level).map(([c, lvl]) => (
                <div key={c}>
                  <span style={{ color: LEVEL_COLOR[lvl] }}>●</span> {c} · {lvl.toLowerCase()}
                  {(snap.verdict_facts[c] ?? []).length > 0 && (
                    <span style={{ color: C.dim }}> — {readFacts(c, snap.verdict_facts[c]).lines.join("; ")}</span>
                  )}
                </div>
              ))}
            </div>
          )}

          <div style={{ marginTop: 14, fontFamily: MONO, fontSize: 10.5, color: C.dim, lineHeight: 1.6 }}>
            the graph is rebuilt from the facts behind each verdict: an edge is drawn wherever a fact
            names a session. sentinel reports levels and facts, not its whole graph, so a clean
            resource stands alone. observe-only: reputation tightens caution, it never denies on its own.
          </div>
        </>
      )}
    </div>
  );
}
