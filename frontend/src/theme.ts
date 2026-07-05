// Shared palette and typography from the mockups — one system, all screens.
export const C = {
  bg: "#12161A", panel: "#191F26", panel2: "#141920", line: "#262E37",
  text: "#D2DAE1", mut: "#78848F", dim: "#4C5760",
  red: "#E5484D", amber: "#F2A33C", yellow: "#D9BC3A", green: "#46A758",
  steel: "#7FA8CC",
} as const;

export const MONO = "ui-monospace, SFMono-Regular, Menlo, monospace";

export const btn = (extra: React.CSSProperties = {}): React.CSSProperties => ({
  display: "flex", alignItems: "center", gap: 6, background: "none",
  border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut,
  fontFamily: MONO, fontSize: 11.5, padding: "6px 12px", cursor: "pointer",
  ...extra,
});

// The audit stream's four-colour taxonomy (spec §8):
//   🔴 red    — fabricated / unrecorded (a denial, a claim with no backing)
//   🟠 orange — policy / memory (a governance fact, a context excision)
//   🟡 yellow — omission / substitution (a fault: a tool result altered or dropped)
//   🟢 green  — consistent (everything that reconciles)
export const sevColor = (verdict: string | null | undefined, kind?: string): string => {
  if (verdict === "deny" || kind === "denial") return C.red;
  if (kind === "fact" || kind === "context_excision") return C.amber;
  if (kind === "fault_injected") return C.yellow;
  return C.green;
};
