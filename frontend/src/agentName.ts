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

/** May the plane break this agent's tools on purpose (inject, fail a tool)?
 * The agent's own word (started with test_bench=True) decides; the
 * per-browser switch in Settings is kept for agents from before that word. */
export function isTestBench(
  node: { profile?: { test_bench?: boolean } | null } | null | undefined, browserBench: boolean,
): boolean {
  return node?.profile?.test_bench === true || browserBench;
}

export const NOT_A_TEST_BENCH =
  "Test-bench only: breaking a tool degrades a real user's answer. Start a copy of the agent "
  + "with no real users as a test bench — PlaneConnector(..., test_bench=True), or AXOR_TEST_BENCH=1.";

// what a plain-Python agent wrapped with axor-wrap's toolset says it is: its
// tool calls are checked, but its model loop is its own — nothing on the plane
// can reach into its context
export const TOOLSET_AGENT = "axor-wrap";

/** Can a cut (removing a piece of context) reach this agent's model? Not
 * behind the proxy, and not for tool functions wrapped without an agent loop. */
export function cannotCut(node: { profile?: { agent?: string } | null } | null | undefined): boolean {
  return behindProxy(node ?? undefined) || node?.profile?.agent === TOOLSET_AGENT;
}

/** The health page's word for it: the piece came from somewhere the agent reads. */
export const NO_TOOLSET_CUT =
  "Nothing here can reach into this agent's context — its tools are wrapped, not its model loop. "
  + "If it came from something the agent reads again (a page, a knowledge-base article, a ticket), "
  + "fix it there: the next run reads it again. To cut context live, wrap the loop (LangChain, Claude).";

export const NO_CUT =
  "This agent's tools are wrapped, not its model loop: nothing here can reach into its context. "
  + "If this text came from something it reads again, fix it there — its next run reads it again. "
  + "To cut context live, wrap the loop (LangChain, Claude).";

// a node is quiet after three missed heartbeats (T = 10 s, protocol §9)
export const QUIET_MS = 30_000;
// the proxy's demo node and demo tree (axor_proxy.governed): they leave the
// plane after a while
export const isDemoNode = (id: string) =>
  /^governed-[0-9a-f]{6}$/.test(id) || /^gov-tree-[0-9a-f]{4}-(orch|research|scraper)$/.test(id);

/** When a demo node left the plane — its last heartbeat, once it is past the
 *  quiet window — or null while it is live. It is said at once, not only
 *  after a command went to it (QA-347). */
export function demoLeftAt(n: { node_id: string; reported?: { updated_ts?: string | null } | null }): string | null {
  const ts = n.reported?.updated_ts;
  return isDemoNode(n.node_id) && ts && Date.now() - Date.parse(ts) > QUIET_MS ? ts : null;
}
