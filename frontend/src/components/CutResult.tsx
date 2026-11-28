import { askPrompt } from "./Ask";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { C, MONO, btn } from "../theme";
import Tooltip from "./Tooltip";
import { NO_CUT, cannotCut } from "../agentName";
import { useHasScope } from "../team";

// Cut this tool result from the agent's context — the same signed command the
// Health panel's self-heal sends, aimed at the result you are looking at. The
// node applies it at its next boundary, once its conversation carries the
// result (an adapter rebuilds per run; see axor-langchain / axor-claude).
/** What a cut without a reason says: it is not sent (QA-367). */
export const CUT_NEEDS_REASON = "not sent: a cut needs a reason — it is recorded with the cut";

export default function CutResult({ runId, valueRef, excised, nodeId: nodeProp }: {
  runId: string; valueRef: string; excised: boolean; nodeId?: string;
}) {
  const qc = useQueryClient();
  // a viewer is not offered the cut (QA-342)
  const canOperate = useHasScope()("operate");
  // ok on an empty reason sent nothing and said nothing (QA-367)
  const [noReason, setNoReason] = useState(false);
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
  // not a live node's run: nothing to command — one that never reported
  // included (QA-340)
  if (!nodeId || !node || !node.reported || !canOperate) return null;
  // a cut it cannot apply would sit "sent" forever
  if (cannotCut(node)) {
    return <div className="mt-2" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>{NO_CUT}</div>;
  }
  const queued = pending?.target_refs?.includes(valueRef);
  return (
    <div className="mt-3 flex items-center gap-2 flex-wrap" style={{ fontFamily: MONO, fontSize: 11 }}>
      {excised ? (
        <span style={{ color: C.green }}>cut — this result no longer reaches the model</span>
      ) : queued || cut.isSuccess ? (
        <span style={{ color: C.amber }}>cut sent to {nodeId} — applied when its conversation carries {valueRef}</span>
      ) : (
        <Tooltip content={`${nodeId} stops seeing this result from its next step on (and, for a file it read, the cached copy). The reason you give is recorded.`}>
          <button
            onClick={async () => {
              const reason = await askPrompt(`Cut ${valueRef} from ${nodeId}'s context — reason (recorded):`);
              setNoReason(reason != null && !reason.trim());
              if (reason && reason.trim()) cut.mutate(reason.trim());
            }}
            disabled={cut.isPending}
            style={btn({ color: C.amber, borderColor: C.amber, fontSize: 11, padding: "4px 10px" })}>
            remove this text from the agent's memory
          </button>
        </Tooltip>
      )}
      {cut.isError && <span style={{ color: C.red }}>{(cut.error as Error).message}</span>}
      {noReason && <span data-testid="cut-needs-reason" style={{ color: C.red }}>{CUT_NEEDS_REASON}</span>}
    </div>
  );
}
