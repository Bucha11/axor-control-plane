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
  // Backend API token (local token or an API key). Sent as the bearer on every
  // request when the backend has auth enabled (architecture section 9).
  apiToken: string;
  // Signed command posture (protocol §6). When both are set, operator commands
  // and facts are canonicalized in the browser and signed by the vault signing
  // custody (never the operator key itself — only this token, which authorizes
  // a sign request). Empty => unsigned dev posture (AXOR_ALLOW_UNSIGNED=1).
  signingKeyId: string;
  vaultSigningToken: string;
  // Adoption (spec: quiet-until-wrong, so learning is opt-in). Learn mode reveals
  // per-surface coach notes; `learnSeen` gates the one-time first-visit nudge;
  // `coachDismissed` remembers which notes the user closed.
  learnMode: boolean;
  learnSeen: boolean;
  coachDismissed: string[];
  // Guided tour position: index into TOUR (components/Tour.tsx), null = not
  // running. Persisted, so a mid-tour reload resumes where the user was.
  tourStep: number | null;
  connect: (mode: ConnectionMode, tools?: { name: string; url: string }[]) => void;
  disconnect: () => void;
  setTestBench: (v: boolean) => void;
  setLastRun: (runId: string) => void;
  setApiToken: (token: string) => void;
  setSigningKeyId: (id: string) => void;
  setVaultSigningToken: (token: string) => void;
  setLearnMode: (v: boolean) => void;
  markLearnSeen: () => void;
  dismissCoach: (id: string) => void;
  resetCoach: () => void;
  setTourStep: (step: number | null) => void;
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
      apiToken: "",
      signingKeyId: "",
      vaultSigningToken: "",
      learnMode: false,
      learnSeen: false,
      coachDismissed: [],
      tourStep: null,
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
      setApiToken: (token) => set({ apiToken: token }),
      setSigningKeyId: (id) => set({ signingKeyId: id }),
      setVaultSigningToken: (token) => set({ vaultSigningToken: token }),
      setLearnMode: (v) => set({ learnMode: v, learnSeen: true }),
      markLearnSeen: () => set({ learnSeen: true }),
      dismissCoach: (id) =>
        set((s) => ({
          coachDismissed: s.coachDismissed.includes(id)
            ? s.coachDismissed
            : [...s.coachDismissed, id],
        })),
      resetCoach: () => set({ coachDismissed: [] }),
      setTourStep: (step) => set({ tourStep: step }),
    }),
    { name: "axor-app" },
  ),
);
