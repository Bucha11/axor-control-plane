import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { C, MONO, btn } from "../theme";
import Tooltip from "./Tooltip";

// Cut this tool result from the agent's context — the same signed command the
// Health panel's self-heal sends, aimed at the result you are looking at. The
// node applies it at its next boundary, once its conversation carries the
// result (an adapter rebuilds per run; see axor-langchain / axor-claude).
export default function CutResult({ runId, valueRef, excised, nodeId: nodeProp }: {
  runId: string; valueRef: string; excised: boolean; nodeId?: string;
}) {
  const qc = useQueryClient();
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.listRuns, enabled: !nodeProp });
  const nodeId = nodeProp ?? (runs.data ?? []).find((r) => r.run_id === runId)?.node_id;
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, enabled: !!nodeId });
  const node = (nodes.data ?? []).find((n) => n.node_id === nodeId);
  const pending = node?.desired?.state.pending_excision as { target_refs?: string[] } | undefined;
  const cut = useMutation({
    mutationFn: (reason: string) => api.command(nodeId!, (node?.desired?.version ?? 0) + 1, {
      pending_excision: { id: `exc_${Date.now()}`, target_refs: [valueRef], reason, operator: "op_ui" },
    }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["nodes"] }),
  });
  if (!nodeId || !node) return null;   // not a live node's run: nothing to command
  const queued = pending?.target_refs?.includes(valueRef);
  return (
    <div className="mt-3 flex items-center gap-2 flex-wrap" style={{ fontFamily: MONO, fontSize: 11 }}>
      {excised ? (
        <span style={{ color: C.green }}>cut — this result no longer reaches the model</span>
      ) : queued || cut.isSuccess ? (
        <span style={{ color: C.amber }}>cut sent to {nodeId} — applied when its conversation carries {valueRef}</span>
      ) : (
        <Tooltip content="Redact this tool result from the agent's context (and, for a file it read, the cached copy). A signed operator command; the reason is recorded.">
          <button
            onClick={() => {
              const reason = window.prompt(`Cut ${valueRef} from ${nodeId}'s context — reason (recorded):`);
              if (reason && reason.trim()) cut.mutate(reason.trim());
            }}
            disabled={cut.isPending}
            style={btn({ color: C.amber, borderColor: C.amber, fontSize: 11, padding: "4px 10px" })}>
            cut this result from {nodeId}
          </button>
        </Tooltip>
      )}
      {cut.isError && <span style={{ color: C.red }}>{(cut.error as Error).message}</span>}
    </div>
  );
}
