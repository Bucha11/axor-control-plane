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

// A production agent behind the guard proxy says so on the plane (axor-proxy
// guard: node profile `agent`). The proxy sees the agent's tool calls, never
// its model: a health check (axor-probe asks the agent test questions) and
// Sentinel's reputation (fed from the agent's own loop) need the agent wrapped
// in code — screens say that instead of an empty "not probed".
export const PROXY_AGENT = "axor-proxy";

export function behindProxy(node: { profile?: { agent?: string } | null } | undefined): boolean {
  return node?.profile?.agent === PROXY_AGENT;
}
