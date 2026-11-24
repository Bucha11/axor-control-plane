// Once the workspace's own agents report, the product's demo is out of the way
// everywhere — the example fleet, example checks, example rules, mock tools,
// "try it on the example". The backend hides the example agents from its lists
// from that moment (examples.work_started); this is the one place the screens
// ask the same question, so no screen keeps offering what the others hide.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import { useApp } from "./store";

/** True once the workspace's own agents have reported (never while loading). */
export function useWorkStarted(): boolean {
  const apiToken = useApp((s) => s.apiToken);
  const ws = useQuery({ queryKey: ["workspace", apiToken], queryFn: api.workspace,
                        staleTime: 60_000, retry: false });
  return ws.data?.work_started === true;
}
