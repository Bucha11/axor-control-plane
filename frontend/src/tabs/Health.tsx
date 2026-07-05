// Health check + reason-required self-heal (health-selfheal mockup).
// Phases are simulated until the adapter lands; the panel shows the contract.
import { useState } from "react";
import { RefreshCw, HeartPulse, Check, Circle } from "lucide-react";
import { C, MONO } from "../theme";

type FamState = "ok" | "drift" | "healed";
type Phase = "idle" | "reason" | "healing" | "reprobe" | "done";

interface Fam {
  name: string;
  st: FamState;
}

const INIT: Fam[] = [
  { name: "tool honesty", st: "ok" },
  { name: "context recall", st: "ok" },
  { name: "refusal drift", st: "drift" },
  { name: "format stability", st: "ok" },
];

export default function Health() {
  const [fams, setFams] = useState<Fam[]>(INIT);
  const [phase, setPhase] = useState<Phase>("idle");
  const [reason, setReason] = useState("");
  const [healedAt, setHealedAt] = useState<string | null>(null);

  const trigger = () => {
    setPhase("healing");
    setTimeout(() => {
      setPhase("reprobe");
      setTimeout(() => {
        setFams((fs) => fs.map((f) => (f.st === "drift" ? { ...f, st: "healed" } : f)));
        setHealedAt("12:41");
        setPhase("done");
      }, 1300);
    }, 1300);
  };

  const drifting = fams.some((f) => f.st === "drift");
  const col = (st: FamState) => (st === "ok" || st === "healed" ? C.green : C.amber);

  return (
    <div style={{ maxWidth: 560, margin: "0 auto" }}>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 14 }}>
        probe integration lands with the adapter (phase 5) — panel shows the interaction contract
      </div>

      <h1 style={{ fontSize: 22, fontWeight: 650, margin: "0 0 4px" }}>
        {drifting ? <>One probe family is drifting.</> : <>Agent behavior is on baseline.</>}
      </h1>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginBottom: 20 }}>banking-assistant · last check 12:38 · 23 probes</div>

      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        {fams.map((f, i) => (
          <div key={f.name} style={{ borderTop: i ? `1px solid ${C.line}` : "none" }}>
            <div className="flex items-center gap-3 px-4 py-3">
              {phase === "reprobe" && (f.st === "drift" || f.st === "healed")
                ? <RefreshCw size={12} color={C.steel} className="animate-spin" />
                : <Circle size={9} fill={col(f.st)} color={col(f.st)} />}
              <span style={{ fontFamily: MONO, fontSize: 13, color: f.st === "drift" ? C.text : C.mut, flex: 1 }}>{f.name}</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: col(f.st), fontWeight: f.st === "drift" ? 700 : 400 }}>
                {f.st === "ok" ? "OK"
                  : f.st === "drift" ? (phase === "healing" ? "healing…" : phase === "reprobe" ? "re-probing…" : "DRIFT")
                  : `healed by op_dmitrii ${healedAt} → re-probe: OK`}
              </span>
            </div>

            {/* self-heal affordance lives with the drift it addresses */}
            {f.st === "drift" && phase === "idle" && (
              <div className="px-4 pb-3 flex items-center gap-3" style={{ paddingLeft: 40 }}>
                <span style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
                  Refusal patterns diverge from baseline (Δ 0.31). Self-heal can re-anchor them.
                </span>
                <button onClick={() => setPhase("reason")}
                  style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.steel}`, borderRadius: 5, color: C.steel, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: "pointer", whiteSpace: "nowrap" }}>
                  <HeartPulse size={12} /> Self-heal
                </button>
              </div>
            )}

            {f.st === "drift" && phase === "reason" && (
              <div className="px-4 pb-3 flex flex-col gap-2" style={{ paddingLeft: 40 }}>
                <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
                  plane command · signed ed25519:op_dmitrii · recorded in trace · a running experiment would be marked `intervened`
                </span>
                <div className="flex gap-2">
                  <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)}
                    placeholder="reason (required) — e.g. drift after prompt update, re-anchoring"
                    style={{ flex: 1, background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "6px 8px", outline: "none" }} />
                  <button onClick={() => reason.trim() && trigger()} disabled={!reason.trim()}
                    style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${reason.trim() ? C.steel : C.line}`, borderRadius: 4, color: reason.trim() ? C.steel : C.dim, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: reason.trim() ? "pointer" : "default" }}>
                    <Check size={12} /> heal & re-probe
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between mt-3">
        <span style={{ fontFamily: MONO, fontSize: 11, color: phase === "done" ? C.green : C.dim }}>
          {phase === "done"
            ? "verdict: PASS — heal verified by re-probe; both events in trace"
            : drifting ? "verdict: PASS · 1 drift" : "verdict: PASS"}
        </span>
        <button style={{ display: "flex", alignItems: "center", gap: 6, background: "none", border: `1px solid ${C.line}`, borderRadius: 5, color: C.mut, fontFamily: MONO, fontSize: 11, padding: "5px 12px", cursor: "pointer" }}>
          <RefreshCw size={12} /> Run health check
        </button>
      </div>

      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 14, lineHeight: 1.7 }}>
        Self-heal never fires automatically on a drift verdict — a behavior-correction loop with no human
        in it is exactly what this product exists to prevent. The verdict recommends; you trigger.
      </div>
    </div>
  );
}
