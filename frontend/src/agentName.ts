// The plane's own proxy reports its test runs under the node id "proxy". It is
// not an agent of the workspace: it showed in every agent list as if it were
// one — next to refund-copilot in Incidents, in the alert, on the receipt — and
// Chaos offered to break its tools "on its next run", which arms nothing.
export const PLANE_PROXY = "proxy";

export function agentLabel(id: string): string {
  return id === PLANE_PROXY ? "test runs (proxy)" : id;
}

/** Agents a live chaos test or a schedule can be armed on. */
export function liveAgents(ids: string[]): string[] {
  return ids.filter((id) => id !== PLANE_PROXY);
}
