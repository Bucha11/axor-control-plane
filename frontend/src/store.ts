// Client/UI state (architecture section 6: Zustand for UI state, TanStack Query
// for server state). This holds the funnel position — which connection the user
// established — and the current session focus (last run). It is deliberately
// small: everything server-derived stays in Query.
import { create } from "zustand";
import { persist } from "zustand/middleware";

// The connection model (spec section 2). Depth determines the availability
// ladder: demo/proxy see the Eval core; adapter additionally unlocks Control,
// the taint graph, Probe health, and branch attestation.
export type ConnectionMode = "none" | "demo" | "proxy" | "adapter";

export interface ConnectionState {
  mode: ConnectionMode;
  // Tool inventory the user declared (onboarding step 1), reused by the
  // Config Builder (spec section 5 v0.2 note).
  tools: { name: string; url: string }[];
  // The connection is flagged test-bench (spec decision 5): injection and
  // self-heal are available only here.
  testBench: boolean;
}

interface AppState {
  connection: ConnectionState;
  lastRunId: string | null;
  connect: (mode: ConnectionMode, tools?: { name: string; url: string }[]) => void;
  disconnect: () => void;
  setTestBench: (v: boolean) => void;
  setLastRun: (runId: string) => void;
}

export const isAdapter = (mode: ConnectionMode): boolean => mode === "adapter";
export const isConnected = (mode: ConnectionMode): boolean => mode !== "none";

export const MODE_LABEL: Record<ConnectionMode, string> = {
  none: "not connected",
  demo: "demo-mode · mock tools",
  proxy: "proxy · your tools",
  adapter: "adapter · full governance",
};

export const useApp = create<AppState>()(
  persist(
    (set) => ({
      connection: { mode: "none", tools: [], testBench: false },
      lastRunId: null,
      connect: (mode, tools) =>
        set((s) => ({
          connection: {
            mode,
            tools: tools ?? s.connection.tools,
            // demo is inherently a test-bench; real connections opt in.
            testBench: mode === "demo" ? true : s.connection.testBench,
          },
        })),
      disconnect: () =>
        set({ connection: { mode: "none", tools: [], testBench: false }, lastRunId: null }),
      setTestBench: (v) => set((s) => ({ connection: { ...s.connection, testBench: v } })),
      setLastRun: (runId) => set({ lastRunId: runId }),
    }),
    { name: "axor-app" },
  ),
);
