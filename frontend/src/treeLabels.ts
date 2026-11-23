// What a tree's agents are called on screen: the node of the tree they ran
// ("read 3 of 6"), not the kernel's node id. One map for the list, the graph
// and the activity feed, built from the plane's tree summaries.
import type { TreeSummary } from "./api";

export interface TreeMember {
  tree: TreeSummary;
  stage: number;          // 0-based
  node: string;
  index: number;          // 0-based
  count: number;
  agent: string;
  label: string;          // "read 3 of 6"
  status: string;         // the tree's word: running, ok, stopped, failed…
}

export function treeMembers(trees: TreeSummary[] | undefined): Map<string, TreeMember> {
  const out = new Map<string, TreeMember>();
  for (const tree of trees ?? []) {
    (tree.stages ?? []).forEach((stage, k) => {
      for (const n of stage) {
        const count = Math.max(n.count ?? 0, n.runs.length, 1);
        n.runs.forEach((r, i) => {
          if (!r.node_id) return;
          const index = typeof r.index === "number" ? r.index : i;
          out.set(r.node_id, {
            tree, stage: k, node: n.node, index, count, agent: n.agent, status: r.status,
            label: count > 1 ? `${n.node} ${index + 1} of ${count}` : n.node,
          });
        });
      }
    });
  }
  return out;
}

/** A tree run's own word for where it is: "stage 2 of 4 · read". */
export function treeProgress(tree: TreeSummary): string {
  const stages = tree.stages ?? [];
  if (!stages.length) return "";
  const busy = stages.findIndex((s) => s.some((n) => n.runs.some((r) => r.status === "running")));
  const started = stages.reduce((last, s, k) => (s.some((n) => n.runs.length) ? k : last), -1);
  const at = busy >= 0 ? busy : started;
  if (at < 0) return `${stages.length} stages`;
  return `stage ${at + 1} of ${stages.length} · ${stages[at].map((n) => n.node).join(", ")}`;
}
