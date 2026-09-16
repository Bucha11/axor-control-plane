// Client/UI state (architecture section 6: Zustand for UI state, TanStack Query
// for server state). This holds the funnel position — which connection the user
// established — and the current session focus (last run). It is deliberately
// small: everything server-derived stays in Query.
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

// The connection model (spec section 2). Depth determines the availability
// ladder: demo/proxy see the Eval core; adapter additionally unlocks Control,
// value provenance, Probe health, and branch attestation.
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
  // Backend API token: the operator master token, a scoped API key, OR a human's
  // axor-identity access token — whichever is set is sent as the bearer.
  apiToken: string;
  // The axor-identity refresh token, held to renew an expired access token. Set
  // only for a human login; empty for a pasted operator/API token.
  refreshToken: string;
  // Who is logged in, for display (identity login only).
  identityEmail: string;
  // Signed command posture (protocol §6). When both are set, operator commands
  // and facts are canonicalized in the browser and signed by the vault signing
  // custody (never the operator key itself — only this token, which authorizes
  // a sign request). Empty => unsigned dev posture (AXOR_ALLOW_UNSIGNED=1).
  signingKeyId: string;
  vaultSigningToken: string;
  // The other half of the wall (spec v2 Ch.5 §3): its own token, in its own
  // header, so being able to dispense a credential never grants the ability to
  // request a signature. Kept apart in the store for the same reason it is kept
  // apart on the wire.
  vaultCredsToken: string;
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
  // establish an identity session (access + refresh + email); clear it on logout
  setSession: (access: string, refresh: string, email: string) => void;
  clearSession: () => void;
  setSigningKeyId: (id: string) => void;
  setVaultSigningToken: (token: string) => void;
  setVaultCredsToken: (token: string) => void;
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

// Keys whose values are credentials; everything else is UI preference.
const SECRET_KEYS = [
  "apiToken", "refreshToken", "vaultSigningToken", "vaultCredsToken",
] as const;

// One Storage face over two backing stores: session for the secret half of the
// persisted blob, local for the rest. zustand/persist writes a single JSON
// string, so the split happens on the way in and is reassembled on the way out.
// Every access is guarded: storage throws outright in some privacy modes, and
// a half-written or hand-edited entry must degrade to "not signed in" rather
// than break rehydration and with it the whole app.
function safeGet(store: Storage, name: string): string | null {
  try {
    return store.getItem(name);
  } catch {
    return null;
  }
}

function safeSet(store: Storage, name: string, value: string): void {
  try {
    store.setItem(name, value);
  } catch {
    /* storage unavailable — the session simply does not persist */
  }
}

const splitStorage: Storage = {
  get length() {
    return window.localStorage.length;
  },
  key: (i) => window.localStorage.key(i),
  clear: () => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  },
  removeItem: (name) => {
    window.localStorage.removeItem(name);
    window.sessionStorage.removeItem(name);
  },
  getItem: (name) => {
    const durable = safeGet(window.localStorage, name);
    if (durable === null) return null;
    try {
      const parsed = JSON.parse(durable);
      const secret = safeGet(window.sessionStorage, name);
      if (secret !== null) {
        parsed.state = { ...parsed.state, ...JSON.parse(secret).state };
      }
      return JSON.stringify(parsed);
    } catch {
      // Unparseable persisted state: start clean instead of throwing out of
      // rehydration, which would take the app down on load.
      return null;
    }
  },
  setItem: (name, value) => {
    let parsed: { state: Record<string, unknown> };
    try {
      parsed = JSON.parse(value);
    } catch {
      return;
    }
    const secretState: Record<string, unknown> = {};
    const durableState: Record<string, unknown> = { ...parsed.state };
    for (const key of SECRET_KEYS) {
      secretState[key] = parsed.state[key];
      // keep the shape stable for readers that run before rehydration, and
      // overwrite any token an older build left on disk
      durableState[key] = "";
    }
    safeSet(window.sessionStorage, name,
            JSON.stringify({ ...parsed, state: secretState }));
    safeSet(window.localStorage, name,
            JSON.stringify({ ...parsed, state: durableState }));
  },
};

export const useApp = create<AppState>()(
  persist(
    (set) => ({
      connection: { mode: "none", tools: [], testBench: false },
      lastRunId: null,
      apiToken: "",
      refreshToken: "",
      identityEmail: "",
      signingKeyId: "",
      vaultSigningToken: "",
      vaultCredsToken: "",
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
      setSession: (access, refresh, email) =>
        set({ apiToken: access, refreshToken: refresh, identityEmail: email }),
      clearSession: () => set({ apiToken: "", refreshToken: "", identityEmail: "" }),
      setSigningKeyId: (id) => set({ signingKeyId: id }),
      setVaultSigningToken: (token) => set({ vaultSigningToken: token }),
      setVaultCredsToken: (token) => set({ vaultCredsToken: token }),
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
    {
      name: "axor-app",
      // Credentials live in sessionStorage, everything else in localStorage.
      //
      // The bearer token, the identity refresh token and the vault signing
      // token were all persisted to localStorage, which means an XSS on this
      // origin walks away with API access AND the ability to ask the signing
      // custody for signatures over arbitrary payloads. sessionStorage does
      // not make XSS harmless, but it scopes the credential to the tab that
      // obtained it and drops it when that tab closes, instead of leaving it
      // on disk indefinitely.
      //
      // The trade: opening the app in a new tab asks you to sign in again.
      // For a governance console that is the right side of the trade.
      storage: createJSONStorage(() => splitStorage),
      partialize: (s) => s,
    },
  ),
);
