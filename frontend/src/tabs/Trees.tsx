// Agent trees: a tree of coding agents (axor-wrap `tree run`) as one screen —
// planner, readers in parallel, implementer — each node with the agent and model
// it ran on, each run linked to its own plane node, and the context that flowed
// between them: pushed, withheld as untrusted, or cut by an operator.
//
// Cutting goes through the tree's own node and the ordinary signed command door
// (`pending_excision`); the run takes it before its next stage, so the item never
// reaches a later node — pushed or pulled.
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type TreeItem, type TreeNode, type TreeRun } from "../api";
import { navigate } from "../router";
import { useApp } from "../store";
import { C, MONO, btn } from "../theme";
import Tooltip from "../components/Tooltip";

const LIVE_MS = 2000;
const IDLE_MS = 15000;

const STATUS_COLOR: Record<string, string> = {
  running: C.green, ok: C.dim, done: C.dim, failed: C.red, denied: C.red,
};

function Shell({ children }: { children: React.ReactNode }) {
  return <div style={{ maxWidth: 1200, margin: "0 auto" }}>{children}</div>;
}

export default function Trees({ treeRun }: { treeRun?: string }) {
  return treeRun ? <TreeView treeRun={decodeURIComponent(treeRun)} /> : <TreeList />;
}

function TreeList() {
  const trees = useQuery({ queryKey: ["trees"], queryFn: api.trees, refetchInterval: IDLE_MS });
  const rows = trees.data?.trees ?? [];
  return (
    <Shell>
      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 6px" }}>Agent trees</h1>
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, marginBottom: 16 }}>
        Trees of coding agents run with <span style={{ color: C.text }}>axor-wrap tree run</span>:
        which agent and model each node ran on, and what context flowed between them.
      </div>
      {trees.isError && <div style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{(trees.error as Error).message}</div>}
      {trees.isSuccess && rows.length === 0 && (
        <div data-testid="trees-empty" style={{ fontFamily: MONO, fontSize: 12, color: C.dim }}>
          no tree runs yet — write an <span style={{ color: C.text }}>axor.tree.yaml</span> and run{" "}
          <span style={{ color: C.text }}>AXOR_PLANE_URL=… axor-wrap tree run</span>
        </div>
      )}
      {rows.length > 0 && (
        <table data-testid="trees-table" style={{ width: "100%", borderCollapse: "collapse", fontFamily: MONO, fontSize: 12 }}>
          <tbody>
            {rows.map((t) => (
              <tr key={t.tree_run} style={{ borderBottom: `1px solid ${C.line}`, cursor: "pointer" }}
                onClick={() => navigate(`trees/${encodeURIComponent(t.tree_run)}`)}>
                <td style={{ padding: "7px 6px", color: STATUS_COLOR[t.status] ?? C.dim }}>● {t.status}</td>
                <td style={{ padding: "7px 6px", color: C.text }}>{t.task}</td>
                <td style={{ padding: "7px 6px", color: C.mut }}>{t.nodes} nodes · {t.agents} agent runs</td>
                <td style={{ padding: "7px 6px", color: C.dim }}>{t.tree_run}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Shell>
  );
}

function TreeView({ treeRun }: { treeRun: string }) {
  const qc = useQueryClient();
  const [live, setLive] = useState(true);
  const tree = useQuery({
    queryKey: ["tree", treeRun],
    queryFn: async () => {
      const t = await api.tree(treeRun);
      setLive(t.status === "running");
      return t;
    },
    refetchInterval: live ? LIVE_MS : IDLE_MS,
  });
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, refetchInterval: live ? LIVE_MS : IDLE_MS });
  const ent = useQuery({ queryKey: ["entitlements"], queryFn: api.entitlements, retry: false });
  const locked = ent.data?.locked_trees[treeRun];
  const folded = useApp((s) => s.planNoticesFolded);
  const setFolded = useApp((s) => s.setPlanNoticesFolded);
  const self = (nodes.data ?? []).find((n) => n.node_id === treeRun);
  const pending = new Set(
    ((self?.desired?.state.pending_excision as { target_refs?: string[] } | undefined)?.target_refs) ?? []);
  const [error, setError] = useState<string | null>(null);
  const cut = useMutation({
    mutationFn: ({ ref, reason }: { ref: string; reason: string }) =>
      api.command(treeRun, (self?.desired?.version ?? 0) + 1, {
        pending_excision: { id: `exc_${Date.now()}`, target_refs: [ref], reason, operator: "op_ui" },
      }),
    onSuccess: () => { setError(null); void qc.invalidateQueries({ queryKey: ["nodes"] }); },
    onError: (e: Error) => setError(e.message),
  });

  if (tree.isError) {
    return <Shell><div style={{ fontFamily: MONO, fontSize: 12, color: C.mut }}>no tree run {treeRun} on this plane.</div></Shell>;
  }
  const t = tree.data;
  if (!t) return <Shell><div style={{ fontFamily: MONO, fontSize: 12, color: C.dim }}>loading…</div></Shell>;
  const canCut = t.status === "running" && !!self;

  return (
    <Shell>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 6 }}>
        <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => navigate("trees")}>agent trees</span>
        {" · "}{t.tree_run}
      </div>
      <div className="flex items-center gap-3 flex-wrap" style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 22, fontWeight: 650, margin: 0 }}>{t.task}</h1>
        <span data-testid="tree-status" style={{ fontFamily: MONO, fontSize: 12, color: STATUS_COLOR[t.status] ?? C.dim }}>
          ● {t.status}
        </span>
      </div>
      {locked && (
        <div data-testid="tree-plan-lock" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7,
          border: `1px solid ${folded ? C.line : C.amber}`, borderRadius: 8,
          padding: folded ? "4px 14px" : "10px 14px", marginBottom: 16 }}>
          <span style={{ color: C.amber }}>This tree mixes agent families — on the paid plan.</span>{" "}
          {!folded && (
            <>
              The free plan keeps one family per tree (Claude Code writing and Claude Code reviewing is
              free); every session in it is still protected by its hooks.{" "}
              <span style={{ color: C.steel, cursor: "pointer" }} onClick={() => navigate("pricing?for=coding")}>
                Coding plan · $25 / developer / month →
              </span>
            </>
          )}
          <button onClick={() => setFolded(!folded)} data-testid="tree-plan-lock-fold"
            style={{ float: "right", background: "none", border: "none", cursor: "pointer",
                     fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
            {folded ? "▸ why" : "▴ fold"}
          </button>
        </div>
      )}

      <div className="flex gap-3" style={{ alignItems: "stretch", overflowX: "auto", marginBottom: 22 }}>
        {t.stages.map((stage, k) => (
          <div key={k} style={{ minWidth: 240, flex: 1 }}>
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, letterSpacing: "0.08em", marginBottom: 6 }}>
              STAGE {k + 1}
            </div>
            {stage.map((name) => <NodeCard key={name} name={name} node={t.nodes[name]} tree={t} />)}
          </div>
        ))}
      </div>

      <ContextItems tree={t} pending={pending} canCut={canCut}
        onCut={(ref) => {
          const reason = window.prompt(`Cut ${ref} so no later node of this tree sees it — reason (recorded):`);
          if (reason && reason.trim()) cut.mutate({ ref, reason: reason.trim() });
        }} />
      {error && <div style={{ fontFamily: MONO, fontSize: 11, color: C.red, marginTop: 8 }}>{error}</div>}
    </Shell>
  );
}

function NodeCard({ name, node, tree }: { name: string; node?: TreeNode; tree: TreeRun }) {
  if (!node) return null;
  return (
    <div data-testid={`tree-node-${name}`} style={{
      background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: 10, marginBottom: 8,
      fontFamily: MONO, fontSize: 11.5,
    }}>
      <div className="flex items-center gap-2 flex-wrap">
        <span style={{ color: C.text, fontWeight: 600 }}>{name}</span>
        <span style={{ color: C.steel }}>{node.agent}{node.model ? `/${node.model}` : ""}</span>
        {node.count > 1 && <span style={{ color: C.mut }}>×{node.count}</span>}
        {node.access === "read" && <span style={{ color: C.amber }}>read-only</span>}
        <span style={{ color: C.dim }}>→ {node.out}</span>
      </div>
      <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 3 }}>
        {node.runs.length === 0 && <span style={{ color: C.dim }}>waiting</span>}
        {node.runs.map((r) => {
          const flow = tree.flow[`${name}#${r.index}`];
          return (
            <div key={r.index} className="flex items-center gap-2 flex-wrap">
              <span style={{ color: STATUS_COLOR[r.status] ?? C.dim }}>●</span>
              {r.node_id ? (
                <span style={{ color: C.steel, cursor: "pointer" }} title="this run as an agent on the plane"
                  onClick={() => navigate(`govern/${encodeURIComponent(r.node_id!)}`)}>
                  #{r.index} {r.node_id}
                </span>
              ) : <span style={{ color: C.mut }}>#{r.index}</span>}
              <span style={{ color: C.dim }}>{r.status}</span>
              {r.verified !== undefined && (
                <span style={{ color: C.mut }}>{r.verified} verified · <span style={{ color: r.unverified ? C.amber : C.mut }}>{r.unverified} unverified</span></span>
              )}
              {flow && (
                <Tooltip content={`pushed ${flow.pushed.length} item(s) (${flow.tokens} tokens), ${flow.pointers.length} by pointer, ${flow.withheld.length} withheld as untrusted`}>
                  <span style={{ color: flow.withheld.length ? C.amber : C.dim }}>
                    in: {flow.pushed.length}{flow.pointers.length ? `+${flow.pointers.length}↗` : ""}
                    {flow.withheld.length ? ` · ${flow.withheld.length} withheld` : ""}
                  </span>
                </Tooltip>
              )}
              {r.reason && <span style={{ color: C.red }} title={r.reason}>{r.reason.slice(0, 60)}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ContextItems({ tree, pending, canCut, onCut }: {
  tree: TreeRun; pending: Set<string>; canCut: boolean; onCut: (ref: string) => void;
}) {
  const items = useMemo(() => tree.items, [tree.items]);
  if (items.length === 0) return null;
  return (
    <div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 6 }}>
        CONTEXT BETWEEN NODES
      </div>
      <table data-testid="tree-items" style={{ width: "100%", borderCollapse: "collapse", fontFamily: MONO, fontSize: 11.5 }}>
        <tbody>
          {items.map((i: TreeItem) => (
            <tr key={i.ref} data-testid={`tree-item-${i.ref}`} style={{ borderBottom: `1px solid ${C.line}`, opacity: i.excised ? 0.5 : 1 }}>
              <td style={{ padding: "5px 6px", color: C.steel, whiteSpace: "nowrap" }}>{i.ref}</td>
              <td style={{ padding: "5px 6px", whiteSpace: "nowrap" }}>
                {i.excised ? <span style={{ color: C.dim }}>cut</span>
                  : i.untrusted ? <span style={{ color: C.amber }}>withheld · untrusted</span>
                    : i.kind === "finding" ? (i.verified
                      ? <span style={{ color: C.green }}>verified</span>
                      : <span style={{ color: C.amber }}>unverified</span>)
                      : <span style={{ color: C.dim }}>{i.kind}</span>}
              </td>
              <td style={{ padding: "5px 6px", color: C.text, textDecoration: i.excised ? "line-through" : "none" }}>
                {i.headline.split("\n")[0]}
              </td>
              <td style={{ padding: "5px 6px", textAlign: "right", whiteSpace: "nowrap" }}>
                {!i.excised && pending.has(i.ref) && <span style={{ color: C.amber }}>cut sent</span>}
                {!i.excised && !pending.has(i.ref) && canCut && (
                  <button onClick={() => onCut(i.ref)}
                    style={btn({ color: C.amber, borderColor: C.amber, fontSize: 10.5, padding: "2px 8px" })}>
                    cut
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
